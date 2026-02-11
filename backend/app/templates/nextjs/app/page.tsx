export default function Home() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-zinc-950 to-zinc-900">
      <div className="text-center">
        <div className="relative inline-flex items-center justify-center mb-8">
          <div className="absolute w-20 h-20 rounded-full border-2 border-zinc-700 border-t-white animate-spin" />
          <div className="w-3 h-3 rounded-full bg-white animate-pulse" />
        </div>
        <h1 className="text-2xl font-semibold text-white mb-2">
          Generating your clone&hellip;
        </h1>
        <p className="text-zinc-500 text-sm">
          This page will update automatically when files are ready.
        </p>
      </div>
    </div>
  );
}
