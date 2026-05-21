import { useEffect, useState } from "react";
import axios from "axios";
import {
  Loader2,
  AlertCircle,
  Image,
  Video,
  Star,
  Download,
  FileJson,
  Target,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL ?? "";

function assetUrl(path) {
  if (!path) return "";
  if (path.startsWith("http")) return path;
  return `${API}${path}`;
}

function ScoreBar({ label, value }) {
  const pct = Math.round((value || 0) * 100);
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-white/50">{label}</span>
        <span className="text-white/70 font-mono">{pct}%</span>
      </div>
      <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
        <div
          className="h-full bg-violet-500 rounded-full"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function JsonBlock({ title, data }) {
  if (!data) return null;
  return (
    <details className="bg-white/5 border border-white/10 rounded-xl overflow-hidden">
      <summary className="px-4 py-3 cursor-pointer text-sm font-medium text-white/80 flex items-center gap-2">
        <FileJson size={14} className="text-violet-400" />
        {title}
      </summary>
      <pre className="px-4 pb-4 text-xs text-emerald-300/90 font-mono overflow-x-auto max-h-64">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}

export default function ResultsPage({ jobId }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError("");
      try {
        const { data: status } = await axios.get(`${API}/api/jobs/${jobId}`);
        if (status.status === "completed" && status.result) {
          if (!cancelled) setResult(status.result);
          return;
        }
        if (status.status === "completed") {
          const { data } = await axios.get(`${API}/api/results/${jobId}`);
          if (!cancelled) setResult(data);
          return;
        }
        if (status.status === "failed") {
          if (!cancelled) setError(status.error || "Job failed");
          return;
        }
        if (!cancelled) setError("Job still running — open Generate tab to watch progress.");
      } catch (e) {
        if (!cancelled) {
          setError(e.response?.data?.detail || "Could not load results");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (!jobId) {
    return (
      <p className="text-white/50 text-sm text-center py-12">
        No job selected. Run a generation first.
      </p>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-20 text-white/50">
        <Loader2 className="animate-spin text-violet-400" size={32} />
        Loading results…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-3 bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-300">
        <AlertCircle size={16} />
        {error}
      </div>
    );
  }

  const product = result?.product_data;
  const strategy = result?.creative_strategy;
  const images = result?.generated_images || [];
  const videos = result?.generated_videos || [];
  const review = result?.review_report;

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h2 className="text-2xl font-bold tracking-tight">Campaign Results</h2>
        <p className="text-white/50 text-sm font-mono">
          Job {jobId.slice(0, 8)}…
          {result?.total_duration_seconds != null && (
            <span className="ml-3 text-violet-400">
              {result.total_duration_seconds.toFixed(1)}s total
            </span>
          )}
        </p>
      </div>

      {product && (
        <section className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-3">
          <h3 className="font-semibold flex items-center gap-2">
            <Target size={16} className="text-violet-400" />
            Product Research
          </h3>
          <h4 className="text-lg font-bold">{product.title}</h4>
          {product.brand && (
            <p className="text-sm text-white/50">
              {product.brand}
              {product.price ? ` · ${product.price}` : ""}
            </p>
          )}
          <p className="text-sm text-white/70 leading-relaxed">{product.description}</p>
          {product.usp && (
            <p className="text-sm text-violet-300/90">
              <span className="text-white/40">USP: </span>
              {product.usp}
            </p>
          )}
          {product.features?.length > 0 && (
            <ul className="text-sm text-white/60 list-disc pl-5 space-y-1">
              {product.features.slice(0, 6).map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {strategy && (
        <section className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-3">
          <h3 className="font-semibold">Creative Strategy</h3>
          <p className="text-violet-300 font-medium">{strategy.primary_hook}</p>
          {strategy.caption_ideas?.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {strategy.caption_ideas.map((c, i) => (
                <span
                  key={i}
                  className="text-xs bg-violet-500/10 border border-violet-500/20 rounded-full px-3 py-1 text-white/70"
                >
                  {c}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="space-y-4">
        <h3 className="font-semibold flex items-center gap-2">
          <Image size={16} className="text-violet-400" />
          Images ({images.length})
        </h3>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {images.map((img) => {
            const reviewItem = review?.image_reviews?.find(
              (r) => r.asset_index === img.index
            );
            return (
              <div
                key={img.index}
                className="bg-white/5 border border-white/10 rounded-xl overflow-hidden"
              >
                <img
                  src={assetUrl(img.url)}
                  alt={`Generated ${img.index}`}
                  className="w-full aspect-square object-cover"
                />
                <div className="p-3 space-y-2">
                  <div className="flex justify-between items-center text-xs">
                    <span className="text-white/40 font-mono">{img.model}</span>
                    {reviewItem && (
                      <span className="flex items-center gap-1 text-amber-400">
                        <Star size={12} />
                        {(reviewItem.overall_score * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  <a
                    href={assetUrl(img.url)}
                    download
                    className="text-xs text-violet-400 hover:text-violet-300 flex items-center gap-1"
                  >
                    <Download size={12} /> Download
                  </a>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-4">
        <h3 className="font-semibold flex items-center gap-2">
          <Video size={16} className="text-violet-400" />
          Videos ({videos.length})
        </h3>
        <div className="grid md:grid-cols-2 gap-4">
          {videos.map((vid) => (
            <div
              key={vid.index}
              className="bg-white/5 border border-white/10 rounded-xl overflow-hidden"
            >
              <video
                src={assetUrl(vid.url)}
                controls
                className="w-full aspect-video bg-black"
              />
              <div className="p-3 text-xs text-white/40 font-mono">{vid.model}</div>
            </div>
          ))}
        </div>
      </section>

      {review && (
        <section className="bg-white/5 border border-white/10 rounded-2xl p-6 space-y-4">
          <h3 className="font-semibold flex items-center gap-2">
            <Star size={16} className="text-amber-400" />
            Quality Review
          </h3>
          <p className="text-sm text-white/70">{review.summary}</p>
          {review.image_reviews?.[0] && (
            <div className="grid sm:grid-cols-2 gap-3 max-w-lg">
              <ScoreBar label="Brand consistency" value={review.image_reviews[0].brand_consistency} />
              <ScoreBar label="Product accuracy" value={review.image_reviews[0].product_accuracy} />
              <ScoreBar label="Visual quality" value={review.image_reviews[0].visual_quality} />
              <ScoreBar label="Hook strength" value={review.image_reviews[0].hook_strength} />
            </div>
          )}
        </section>
      )}

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-white/60">Raw artifacts</h3>
        <JsonBlock title="Product research JSON" data={product} />
        <JsonBlock title="Creative strategy JSON" data={strategy} />
        <JsonBlock title="Review report JSON" data={review} />
      </div>
    </div>
  );
}
