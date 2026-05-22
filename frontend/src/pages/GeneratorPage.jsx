import { useState, useEffect, useRef } from "react";
import axios from "axios";
import {
  Sparkles, Link, ArrowRight, CheckCircle2, Circle,
  AlertCircle, Loader2, Image, Video, Star
} from "lucide-react";

// Empty string = same-origin (Docker/nginx or Vite dev proxy)
const API = import.meta.env.VITE_API_URL ?? "";

const STEPS = [
  { id: "product_research", label: "Product Research", icon: "🔍", desc: "Scraping & analyzing product page" },
  { id: "creative_strategy", label: "Creative Strategy", icon: "🎯", desc: "Generating hooks, themes & angles" },
  { id: "prompt_generation", label: "Prompt Engineering", icon: "✍️", desc: "Crafting image & video prompts" },
  { id: "media_generation", label: "Media Generation", icon: "🎨", desc: "Generating 5 images + 2 videos" },
  { id: "review_critic", label: "Quality Review", icon: "⭐", desc: "Evaluating & scoring creatives" },
  { id: "completed", label: "Complete", icon: "✅", desc: "All assets ready for download" },
];

function StepItem({ step, status }) {
  const isComplete = status === "complete";
  const isActive = status === "active";
  const isPending = status === "pending";

  return (
    <div className={`flex items-start gap-3 py-3 px-4 rounded-xl transition-all ${
      isActive ? "bg-violet-500/10 border border-violet-500/30" :
      isComplete ? "bg-emerald-500/5 border border-emerald-500/20" :
      "border border-transparent"
    }`}>
      <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-sm flex-shrink-0 mt-0.5 ${
        isComplete ? "bg-emerald-500/20 text-emerald-400" :
        isActive ? "bg-violet-500/20 text-violet-300" :
        "bg-white/5 text-white/30"
      }`}>
        {isComplete ? <CheckCircle2 size={16} /> :
         isActive ? <Loader2 size={16} className="animate-spin" /> :
         step.icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className={`text-sm font-medium ${
          isComplete ? "text-emerald-300" :
          isActive ? "text-white" : "text-white/40"
        }`}>
          {step.label}
        </div>
        <div className={`text-xs mt-0.5 ${isActive ? "text-white/60" : "text-white/25"}`}>
          {step.desc}
        </div>
      </div>
    </div>
  );
}

export default function GeneratorPage({ onJobComplete }) {
  const [url, setUrl] = useState("");
  const [brandName, setBrandName] = useState("");
  const [priority, setPriority] = useState("normal");
  const [extraInstructions, setExtraInstructions] = useState("");
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [progress, setProgress] = useState(0);
  const [currentStep, setCurrentStep] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const pollRef = useRef(null);

  const normalizedStep =
    { queued: "product_research", starting: "product_research" }[currentStep] ||
    currentStep;

  const stepStatuses = STEPS.map((step) => {
    const stepIdx = STEPS.findIndex((s) => s.id === normalizedStep);
    const thisIdx = STEPS.findIndex((s) => s.id === step.id);
    if (status === "completed") return "complete";
    if (thisIdx < stepIdx) return "complete";
    if (thisIdx === stepIdx) return "active";
    return "pending";
  });

  const startPolling = (id) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await axios.get(`${API}/api/jobs/${id}`);
        setStatus(data.status);
        setProgress(data.progress || 0);
        setCurrentStep(data.current_step || "");

        if (data.status === "completed") {
          clearInterval(pollRef.current);
          setResult(data.result);
          onJobComplete?.(id);
        }
        if (data.status === "failed") {
          clearInterval(pollRef.current);
          setError(data.error || "Job failed unexpectedly");
        }
      } catch (e) {
        console.error(e);
      }
    }, 2000);
  };

  const handleSubmit = async () => {
    if (!url.trim()) return;
    setError("");
    setLoading(true);
    setResult(null);
    setStatus("pending");
    setProgress(0);

    try {
      const { data } = await axios.post(`${API}/api/generate`, {
        url,
        brand_name: brandName.trim() || null,
        priority,
        extra_instructions: extraInstructions.trim() || null,
      });
      setJobId(data.job_id);
      setStatus("running");
      startPolling(data.job_id);
    } catch (e) {
      setError(e.response?.data?.detail || "Failed to start job");
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => () => clearInterval(pollRef.current), []);

  const DEMO_URLS = [
    "https://www.amazon.com/dp/B08N5WRWNW",
    "https://www.apple.com/airpods-pro/",
    "https://www.nike.com/t/air-max-270",
  ];

  return (
    <div className="space-y-8">
      {/* Hero header */}
      <div className="text-center space-y-3 py-6">
        <div className="inline-flex items-center gap-2 text-xs text-violet-400 bg-violet-500/10 border border-violet-500/20 rounded-full px-4 py-1.5 mb-2">
          <Sparkles size={12} />
          Multi-Agent AI Pipeline
        </div>
        <h1 className="text-4xl font-bold tracking-tight bg-gradient-to-br from-white to-white/50 bg-clip-text text-transparent">
          Product Creative Generator
        </h1>
        <p className="text-white/50 max-w-lg mx-auto text-sm leading-relaxed">
          Paste any product page URL. 6 AI agents will research it, craft a strategy, 
          and generate 5 marketing images + 2 videos automatically.
        </p>
      </div>

      {/* URL Input */}
      <div className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-4">
        <label className="text-sm font-medium text-white/70 flex items-center gap-2">
          <Link size={14} />
          Product Page URL
        </label>
        <div className="flex gap-3">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            placeholder="https://www.amazon.com/dp/... or any product page"
            disabled={loading || status === "running"}
            className="flex-1 bg-white/5 border border-white/15 rounded-xl px-4 py-3 text-sm text-white placeholder-white/25 focus:outline-none focus:border-violet-500/60 focus:bg-violet-500/5 transition-all disabled:opacity-50"
          />
          <button
            onClick={handleSubmit}
            disabled={loading || !url.trim() || status === "running"}
            className="px-6 py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl text-sm font-semibold flex items-center gap-2 transition-all"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
            Generate
          </button>
        </div>

        <div className="grid md:grid-cols-3 gap-3">
          <input
            type="text"
            value={brandName}
            onChange={(e) => setBrandName(e.target.value)}
            placeholder="Brand name (optional)"
            disabled={loading || status === "running"}
            className="bg-white/5 border border-white/15 rounded-xl px-4 py-3 text-sm text-white placeholder-white/25 focus:outline-none focus:border-violet-500/60 focus:bg-violet-500/5 transition-all disabled:opacity-50"
          />
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            disabled={loading || status === "running"}
            className="bg-white/5 border border-white/15 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-violet-500/60 focus:bg-violet-500/5 transition-all disabled:opacity-50"
          >
            <option value="high">High priority</option>
            <option value="normal">Normal priority</option>
            <option value="low">Low priority</option>
          </select>
          <input
            type="text"
            value={extraInstructions}
            onChange={(e) => setExtraInstructions(e.target.value)}
            placeholder="Extra creative instructions (optional)"
            disabled={loading || status === "running"}
            className="bg-white/5 border border-white/15 rounded-xl px-4 py-3 text-sm text-white placeholder-white/25 focus:outline-none focus:border-violet-500/60 focus:bg-violet-500/5 transition-all disabled:opacity-50"
          />
        </div>

        {/* Demo URLs */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-white/30">Try:</span>
          {DEMO_URLS.map((demoUrl) => (
            <button
              key={demoUrl}
              onClick={() => setUrl(demoUrl)}
              className="text-xs text-violet-400 hover:text-violet-300 bg-violet-500/10 hover:bg-violet-500/20 px-3 py-1 rounded-full transition-all"
            >
              {new URL(demoUrl).hostname.replace("www.", "")}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-3 bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-300">
          <AlertCircle size={16} className="flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Progress */}
      {(status === "running" || status === "retrying") && (
        <div className="grid md:grid-cols-2 gap-6">
          {/* Progress bar */}
          <div className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm">Processing</h3>
              <span className="text-violet-400 font-mono text-sm">{progress}%</span>
            </div>
            <div className="w-full bg-white/10 rounded-full h-2 overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-violet-500 to-fuchsia-500 rounded-full transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-xs text-white/40 font-mono">{currentStep || "initializing..."}</p>
          </div>

          {/* Agent steps */}
          <div className="bg-white/5 border border-white/10 rounded-2xl p-4 space-y-1">
            {STEPS.map((step, i) => (
              <StepItem key={step.id} step={step} status={stepStatuses[i]} />
            ))}
          </div>
        </div>
      )}

      {/* Completed */}
      {status === "completed" && result && (
        <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-2xl p-6 space-y-4">
          <div className="flex items-center gap-3">
            <CheckCircle2 size={20} className="text-emerald-400" />
            <h3 className="font-semibold text-emerald-300">Generation Complete!</h3>
            <span className="text-xs text-emerald-400/60 font-mono ml-auto">
              {result.total_duration_seconds?.toFixed(1)}s
            </span>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white/5 rounded-xl p-4 text-center">
              <div className="text-2xl font-bold text-white">{result.generated_images?.length || 0}</div>
              <div className="text-xs text-white/50 flex items-center justify-center gap-1 mt-1">
                <Image size={12} /> Images
              </div>
            </div>
            <div className="bg-white/5 rounded-xl p-4 text-center">
              <div className="text-2xl font-bold text-white">{result.generated_videos?.length || 0}</div>
              <div className="text-xs text-white/50 flex items-center justify-center gap-1 mt-1">
                <Video size={12} /> Videos
              </div>
            </div>
            <div className="bg-white/5 rounded-xl p-4 text-center">
              <div className="text-2xl font-bold text-white">
                {result.review_report
                  ? (result.review_report.image_reviews?.reduce((a, r) => a + r.overall_score, 0) /
                     Math.max(result.review_report.image_reviews?.length, 1)).toFixed(2)
                  : "—"}
              </div>
              <div className="text-xs text-white/50 flex items-center justify-center gap-1 mt-1">
                <Star size={12} /> Avg Score
              </div>
            </div>
          </div>

          <button
            onClick={() => onJobComplete?.(jobId)}
            className="w-full py-3 bg-violet-600 hover:bg-violet-500 rounded-xl text-sm font-semibold transition-all flex items-center justify-center gap-2"
          >
            View Full Results <ArrowRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}