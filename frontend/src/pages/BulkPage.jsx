import { useState, useRef } from "react";
import axios from "axios";
import { Upload, FileText, CheckCircle2, AlertCircle, Loader2, ExternalLink } from "lucide-react";

const API = import.meta.env.VITE_API_URL ?? "";

const SAMPLE_CSV = `url,brand_name,priority,extra_instructions
https://www.apple.com/airpods-pro/,Apple,high,Emphasize noise cancellation and premium feel
https://www.sony.com/electronics/headband-headphones/wh-1000xm5,Sony,normal,Focus on comfort for long listening sessions
https://www.nike.com/t/air-max-270-mens-shoes-KkLcGR,Nike,normal,Highlight lifestyle and streetwear vibe`;

export default function BulkPage() {
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [parseErrors, setParseErrors] = useState([]);
  const [error, setError] = useState("");
  const fileRef = useRef();
  const pollRef = useRef();

  const handleFile = (f) => {
    if (!f) return;
    if (!f.name.endsWith(".csv")) {
      setError("Please upload a .csv file");
      return;
    }
    setError("");
    setParseErrors([]);
    setFile(f);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    handleFile(e.dataTransfer.files[0]);
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError("");
    setParseErrors([]);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const { data } = await axios.post(`${API}/api/bulk`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setBatchResult(data);
      setParseErrors(data.errors || []);
      startPollingBatch(data.batch_id);
    } catch (e) {
      setError(e.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const startPollingBatch = (batchId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await axios.get(`${API}/api/bulk/${batchId}`);
        setBatchStatus(data);
        if (data.completed + data.failed >= data.total) {
          clearInterval(pollRef.current);
        }
      } catch (e) {
        console.error(e);
      }
    }, 3000);
  };

  const downloadSampleCSV = () => {
    const blob = new Blob([SAMPLE_CSV], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sample_products.csv";
    a.click();
  };

  const completedPct = batchStatus
    ? Math.round(((batchStatus.completed + batchStatus.failed) / batchStatus.total) * 100)
    : 0;

  return (
    <div className="space-y-8 max-w-3xl mx-auto">
      <div className="space-y-2">
        <h2 className="text-2xl font-bold tracking-tight">Bulk CSV Processing</h2>
        <p className="text-white/50 text-sm">
          Upload a CSV with multiple product URLs to process them all asynchronously.
          Track job progress in real-time.
        </p>
      </div>

      {/* CSV Format Reference */}
      <div className="bg-white/5 border border-white/10 rounded-2xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white/80">CSV Format</h3>
          <button
            onClick={downloadSampleCSV}
            className="text-xs text-violet-400 hover:text-violet-300 flex items-center gap-1 transition-colors"
          >
            Download Sample <ExternalLink size={12} />
          </button>
        </div>
        <pre className="bg-black/40 rounded-xl p-4 text-xs text-emerald-300 font-mono overflow-x-auto">
{SAMPLE_CSV}
        </pre>
        <div className="text-xs text-white/30 space-y-1">
          <div><span className="text-white/60">url</span> — Product page URL (required)</div>
          <div><span className="text-white/60">brand_name</span> — Override brand name (optional)</div>
          <div><span className="text-white/60">priority</span> — high / normal / low (optional)</div>
          <div><span className="text-white/60">extra_instructions</span> — Extra creative guidance (optional)</div>
        </div>
      </div>

      {/* Drop Zone */}
      <div
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => fileRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-all ${
          file
            ? "border-violet-500/50 bg-violet-500/5"
            : "border-white/15 hover:border-white/30 hover:bg-white/3"
        }`}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(e) => handleFile(e.target.files[0])}
        />
        {file ? (
          <div className="space-y-2">
            <FileText size={32} className="mx-auto text-violet-400" />
            <div className="font-medium text-violet-300">{file.name}</div>
            <div className="text-sm text-white/40">
              {(file.size / 1024).toFixed(1)} KB — Click to replace
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <Upload size={32} className="mx-auto text-white/30" />
            <div className="text-white/60 font-medium">Drop your CSV here</div>
            <div className="text-sm text-white/30">or click to browse</div>
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-3 bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-300">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {parseErrors.length > 0 && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-4 text-sm text-amber-200 space-y-1">
          <div className="font-semibold">Rows skipped during parsing</div>
          {parseErrors.slice(0, 6).map((msg, i) => (
            <div key={i}>{msg}</div>
          ))}
          {parseErrors.length > 6 && (
            <div>...and {parseErrors.length - 6} more</div>
          )}
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={!file || uploading}
        className="w-full py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed rounded-xl font-semibold flex items-center justify-center gap-2 transition-all"
      >
        {uploading ? (
          <><Loader2 size={16} className="animate-spin" /> Uploading...</>
        ) : (
          <><Upload size={16} /> Start Bulk Processing</>
        )}
      </button>

      {/* Batch Status */}
      {batchResult && (
        <div className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold">Batch {batchResult.batch_id.slice(0, 8)}…</h3>
            <span className="text-xs text-white/40 font-mono">{batchResult.total_jobs} jobs</span>
          </div>

          {batchStatus && (
            <>
              <div className="grid grid-cols-4 gap-3 text-center">
                {[
                  { label: "Total", value: batchStatus.total, color: "text-white" },
                  { label: "Done", value: batchStatus.completed, color: "text-emerald-400" },
                  { label: "Running", value: batchStatus.running, color: "text-violet-400" },
                  { label: "Failed", value: batchStatus.failed, color: "text-red-400" },
                ].map((stat) => (
                  <div key={stat.label} className="bg-white/5 rounded-xl p-3">
                    <div className={`text-xl font-bold ${stat.color}`}>{stat.value}</div>
                    <div className="text-xs text-white/40 mt-1">{stat.label}</div>
                  </div>
                ))}
              </div>

              <div className="space-y-2">
                <div className="flex justify-between text-xs text-white/50">
                  <span>Progress</span>
                  <span>{completedPct}%</span>
                </div>
                <div className="w-full bg-white/10 rounded-full h-2 overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-violet-500 to-fuchsia-500 rounded-full transition-all duration-1000"
                    style={{ width: `${completedPct}%` }}
                  />
                </div>
              </div>

              {/* Individual job list */}
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {batchStatus.jobs?.map((job) => (
                  <div key={job.job_id} className="flex items-center gap-3 py-2 px-3 bg-white/3 rounded-lg">
                    {job.status === "completed" ? (
                      <CheckCircle2 size={14} className="text-emerald-400 flex-shrink-0" />
                    ) : job.status === "failed" ? (
                      <AlertCircle size={14} className="text-red-400 flex-shrink-0" />
                    ) : (
                      <Loader2 size={14} className="text-violet-400 animate-spin flex-shrink-0" />
                    )}
                    <span className="text-xs font-mono text-white/60 flex-1 truncate">{job.job_id}</span>
                    <span className={`text-xs font-mono ${
                      job.status === "completed" ? "text-emerald-400" :
                      job.status === "failed" ? "text-red-400" : "text-violet-400"
                    }`}>
                      {job.progress}%
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}