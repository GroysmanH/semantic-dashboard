/** The slice of Vega's View we need. Typed here rather than imported so the
 *  export path does not depend on vega-typings resolving. */
export interface VegaView {
  toImageURL(type: "png" | "svg", scaleFactor?: number): Promise<string>;
}

/** Rasterise a chart. scale 2 so the image is legible when pasted into a
 *  document rather than viewed at card size. */
export async function chartPng(view: VegaView, scale = 2): Promise<string> {
  return view.toImageURL("png", scale);
}

/** Browsers will not save a data: URL without an anchor to click, and the
 *  anchor must be in the document for Firefox. */
export function download(href: string, filename: string): void {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  download(url, filename);
  // Revoking immediately races the click in Safari; a tick is enough.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Filenames are user-visible and end up in a downloads folder next to
 *  everything else, so they carry the card title and the date. */
export function safeName(title: string, ext: string): string {
  const stem = (title || "untitled")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "untitled";
  const day = new Date().toISOString().slice(0, 10);
  return `${stem}-${day}.${ext}`;
}
