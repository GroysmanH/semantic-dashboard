import { useEffect, useState } from "react";
import { getJSON } from "./api/client";

export default function App() {
  const [health, setHealth] = useState<string>("checking...");

  useEffect(() => {
    getJSON<{ status: string }>("/health")
      .then((r) => setHealth(r.status))
      .catch((e) => setHealth(`unreachable: ${e.message}`));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: "2rem" }}>
      <h1>Semantic Dashboard</h1>
      <p>backend: {health}</p>
    </main>
  );
}
