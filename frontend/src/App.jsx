import { useState } from "react";
import GeneratorPage from "./pages/GeneratorPage";
import BulkPage from "./pages/BulkPage";
import ResultsPage from "./pages/ResultsPage";

export default function App() {
  const [page, setPage] = useState("generator");
  const [activeJobId, setActiveJobId] = useState(null);

  const navigate = (to, jobId = null) => {
    setPage(to);
    if (jobId) setActiveJobId(jobId);
  };

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white font-sans">
      {/* Nav */}
      <nav className="border-b border-white/10 bg-[#0d0d14]/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center text-sm font-bold">
              AI
            </div>
            <span className="font-semibold tracking-tight">Creative Workflow</span>
            <span className="text-xs text-white/30 font-mono">v1.0</span>
          </div>
          <div className="flex items-center gap-1">
            {[
              { id: "generator", label: "Generate" },
              { id: "bulk", label: "Bulk CSV" },
              ...(activeJobId ? [{ id: "results", label: "Results" }] : []),
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => navigate(tab.id)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  page === tab.id
                    ? "bg-violet-600/30 text-violet-300 border border-violet-500/40"
                    : "text-white/50 hover:text-white/80 hover:bg-white/5"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </nav>

      {/* Pages */}
      <main className="max-w-6xl mx-auto px-6 py-10">
        {page === "generator" && (
          <GeneratorPage onJobComplete={(jobId) => navigate("results", jobId)} />
        )}
        {page === "bulk" && <BulkPage />}
        {page === "results" && <ResultsPage jobId={activeJobId} />}
      </main>
    </div>
  );
}