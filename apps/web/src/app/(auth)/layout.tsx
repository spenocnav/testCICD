export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden px-4 py-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            'radial-gradient(60% 80% at 0% 0%, rgba(238,46,47,0.10), transparent 60%),' +
            'radial-gradient(55% 75% at 100% 0%, rgba(238,46,47,0.06), transparent 55%),' +
            'radial-gradient(70% 80% at 100% 100%, rgba(195,202,200,0.35), transparent 60%),' +
            'linear-gradient(180deg, #fafafa 0%, #f4f5f5 100%)',
        }}
      />
      {children}
    </main>
  );
}
