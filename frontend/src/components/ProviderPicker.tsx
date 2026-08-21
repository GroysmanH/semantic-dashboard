import type { Provider } from "../api/client";

/** Named by the model, not the vendor: "DeepSeek" is what someone asked
 *  for, even though the key and the hosting are NVIDIA's. */
export const PROVIDER_LABEL: Record<Provider, string> = {
  anthropic: "Claude",
  gemini: "Gemini",
  openai: "GPT",
  nvidia: "DeepSeek",
};

/** One control, used by the chat composer and by a blank card.
 *
 *  A dropdown rather than a row of buttons: the choice is made rarely and
 *  changes everything after it, so it reads better as a stated setting than
 *  as four things competing for attention next to the question box.
 */
export default function ProviderPicker({
  provider,
  providers,
  busy,
  label = "Ask",
  onChange,
}: {
  provider: Provider;
  providers: Provider[];
  busy: boolean;
  label?: string;
  onChange: (p: Provider) => void;
}) {
  // One configured key is not a decision worth putting in front of anyone.
  if (providers.length < 2) return null;

  return (
    <label className="provider-picker">
      <span className="eyebrow">{label}</span>
      <select
        value={provider}
        disabled={busy}
        aria-label="Model"
        onChange={(e) => onChange(e.target.value as Provider)}
      >
        {providers.map((p) => (
          <option key={p} value={p}>
            {PROVIDER_LABEL[p] ?? p}
          </option>
        ))}
      </select>
    </label>
  );
}
