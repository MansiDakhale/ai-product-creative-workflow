"""
graph.py
LangGraph Agent Orchestration

Defines the multi-agent state machine that orchestrates all 6 agents.
Uses conditional edges to handle retries from the Review/Critic agent.

Graph structure:
    START
        → product_research
        → creative_strategy
        → prompt_generation
        → image_generation
        → video_generation
        → review_critic
        → END  (or back to image/video gen if review fails and retries remain)
"""

from __future__ import annotations
import asyncio
import os
import structlog
from datetime import datetime, timezone
from typing import Literal

from langgraph.graph import StateGraph, END, START

from models.schemas import WorkflowState, JobStatus, Priority
from utils.progress import report_progress
from utils.storage import save_workflow_artifacts
from agents.product_research import run_product_research
from agents.creative_strategy import run_creative_strategy
from agents.prompt_generation import run_prompt_generation
from agents.image_generation import run_image_generation
from agents.video_generation import run_video_generation
from agents.review_critic import run_review_critic

logger = structlog.get_logger()


def get_groq_client():
    """Lazy-initialize Groq client with certifi CA bundle (Windows SSL fix)."""
    from groq import Groq
    from utils.http_client import make_sync_client

    return Groq(
        api_key=os.environ["GROQ_API_KEY"],
        http_client=make_sync_client(timeout=120.0),
    )


# ─── Node functions (wrap async agents as sync for LangGraph) ─────────────────

async def node_product_research(state: WorkflowState) -> WorkflowState:
    report_progress(state.job_id, "product_research", 15)
    groq = get_groq_client()
    state = await run_product_research(state, groq)
    if state.product_data:
        save_workflow_artifacts(state)
    report_progress(state.job_id, "product_research", 25)
    return state


async def node_creative_strategy(state: WorkflowState) -> WorkflowState:
    report_progress(state.job_id, "creative_strategy", 30)
    groq = get_groq_client()
    state = await run_creative_strategy(state, groq)
    if state.creative_strategy:
        save_workflow_artifacts(state)
    report_progress(state.job_id, "creative_strategy", 40)
    return state


async def node_prompt_generation(state: WorkflowState) -> WorkflowState:
    report_progress(state.job_id, "prompt_generation", 45)
    groq = get_groq_client()
    state = await run_prompt_generation(state, groq)
    if state.generated_prompts:
        save_workflow_artifacts(state)
    report_progress(state.job_id, "prompt_generation", 50)
    return state


async def node_image_generation(state: WorkflowState) -> WorkflowState:
    report_progress(state.job_id, "image_generation", 55)
    state = await run_image_generation(state)
    if state.generated_images:
        save_workflow_artifacts(state)
    report_progress(state.job_id, "image_generation", 70)
    return state


async def node_video_generation(state: WorkflowState) -> WorkflowState:
    report_progress(state.job_id, "video_generation", 72)
    state = await run_video_generation(state)
    if state.generated_videos:
        save_workflow_artifacts(state)
    report_progress(state.job_id, "video_generation", 80)
    return state


async def node_review_critic(state: WorkflowState) -> WorkflowState:
    report_progress(state.job_id, "review_critic", 85)
    groq = get_groq_client()
    state = await run_review_critic(state, groq)
    if state.review_report:
        save_workflow_artifacts(state)
    report_progress(state.job_id, "review_critic", 95)
    return state


async def node_finalize(state: WorkflowState) -> WorkflowState:
    """Mark the job as complete and set timing metadata."""
    report_progress(state.job_id, "completed", 100)
    save_workflow_artifacts(state)
    state.status = JobStatus.COMPLETED
    state.completed_at = datetime.now(timezone.utc)
    if state.started_at:
        delta = state.completed_at - state.started_at
        state.total_duration_seconds = round(delta.total_seconds(), 2)
    logger.info(
        "workflow_complete",
        job_id=state.job_id,
        duration_s=state.total_duration_seconds,
        images=len(state.generated_images),
        videos=len(state.generated_videos),
    )
    return state


# ─── Conditional edge functions ───────────────────────────────────────────────

def should_retry(state: WorkflowState) -> Literal["image_generation", "finalize"]:
    """
    After review: if retry is recommended and this is the first attempt, loop back.
    Otherwise, proceed to finalization.
    """
    if (
        state.review_report
        and state.review_report.retry_recommended
        and state.retry_count < 1
    ):
        logger.info(
            "retry_triggered",
            job_id=state.job_id,
            attempt=state.retry_count + 1,
        )
        state.retry_count += 1
        state.status = JobStatus.RETRYING
        return "image_generation"

    if state.review_report and state.review_report.retry_recommended:
        logger.info(
            "retry_limit_reached",
            job_id=state.job_id,
            retry_count=state.retry_count,
            max_retries=1,
        )
    return "finalize"


def should_continue_after_research(state: WorkflowState) -> Literal["creative_strategy", END]:
    if state.status == JobStatus.FAILED:
        return END
    return "creative_strategy"


def should_continue_after_strategy(state: WorkflowState) -> Literal["prompt_generation", END]:
    if state.status == JobStatus.FAILED:
        return END
    return "prompt_generation"


# ─── Build the graph ──────────────────────────────────────────────────────────

def build_workflow_graph() -> StateGraph:
    """
    Construct and compile the LangGraph state machine.
    
    Returns:
        Compiled LangGraph app ready for invocation.
    """
    workflow = StateGraph(WorkflowState)

    # ── Register nodes ──────────────────────────────────────────────────────
    workflow.add_node("product_research", node_product_research)
    workflow.add_node("creative_strategy_node", node_creative_strategy)
    workflow.add_node("prompt_generation", node_prompt_generation)
    workflow.add_node("image_generation", node_image_generation)
    workflow.add_node("video_generation", node_video_generation)
    workflow.add_node("review_critic", node_review_critic)
    workflow.add_node("finalize", node_finalize)

    # ── Define edges ────────────────────────────────────────────────────────
    workflow.add_edge(START, "product_research")

    workflow.add_conditional_edges(
        "product_research",
        should_continue_after_research,
        {"creative_strategy": "creative_strategy_node", END: END},
    )

    workflow.add_conditional_edges(
        "creative_strategy_node",
        should_continue_after_strategy,
        {"prompt_generation": "prompt_generation", END: END},
    )

    workflow.add_edge("prompt_generation", "image_generation")
    workflow.add_edge("image_generation", "video_generation")
    workflow.add_edge("video_generation", "review_critic")

    # Retry loop: review_critic → media_generation (if retry) OR finalize
    workflow.add_conditional_edges(
        "review_critic",
        should_retry,
        {"image_generation": "image_generation", "finalize": "finalize"},
    )

    workflow.add_edge("finalize", END)

    return workflow.compile()


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_workflow(
    url: str,
    job_id: str | None = None,
    brand_name: str | None = None,
    extra_instructions: str | None = None,
    priority: Priority | None = None,
) -> WorkflowState:
    """
    Run the full multi-agent workflow for a given product URL.
    
    Args:
        url: Product page URL to process
        job_id: Optional pre-assigned job ID
    
    Returns:
        Final WorkflowState with all generated assets
    """
    resolved_priority = Priority.NORMAL
    if priority is not None:
        try:
            resolved_priority = Priority(priority)
        except Exception:
            resolved_priority = Priority.NORMAL

    initial_state = WorkflowState(
        url=url,
        started_at=datetime.now(timezone.utc),
        status=JobStatus.RUNNING,
        brand_name=brand_name,
        extra_instructions=extra_instructions,
        priority=resolved_priority,
    )
    if job_id:
        initial_state.job_id = job_id

    app = build_workflow_graph()
    final_state = await app.ainvoke(initial_state)
    return final_state