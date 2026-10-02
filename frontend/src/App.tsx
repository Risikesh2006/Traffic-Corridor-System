import "./App.css";

function App() {
  return (
    <div className="app-shell">
      <header className="top-bar">
        <span className="product-name">Corridor Control</span>
        <span className="mode-badge mode-normal">NORMAL</span>
      </header>
      <main className="landing">
        <p>Emergency Traffic Corridor — digital twin control system.</p>
        <p className="hint">
          Operator, Crew and Hospital views land in later phases (see
          backend/corridor and docs/design.md).
        </p>
      </main>
    </div>
  );
}

export default App;
