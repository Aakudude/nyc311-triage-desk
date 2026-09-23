export function LoadingState({ waking = false }: { waking?: boolean }) {
  return (
    <div className="state-card" role="status">
      <span className="loader" aria-hidden="true" />
      <div>
        <strong>{waking ? "Waking the API" : "Loading operations data"}</strong>
        <p>{waking ? "Render may take up to a minute after sleeping. Retrying automatically…" : "Preparing the latest replay snapshot…"}</p>
      </div>
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="state-card state-error" role="alert">
      <div>
        <strong>We could not load this view</strong>
        <p>{message}</p>
      </div>
      {retry && <button className="button button-secondary" onClick={retry}>Try again</button>}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}
