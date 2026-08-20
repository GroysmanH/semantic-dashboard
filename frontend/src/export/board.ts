import type { Card } from "../api/client";
import { chartPng, type VegaView } from "./png";

const SCALE = 2;          // device-ish resolution without a dependency
const COLS = 12;
const COL_W = 110;        // export pixels per grid column
const ROW_H = 34;         // export pixels per grid row
const PAD = 18;
const CAPTION_BASE = 46;  // title + freshness line
const LINE = 14;          // restatement line height
const MAX_LINES = 3;      // beyond this a restatement is genuinely long

const INK = "#16201c";
const FAINT = "#8b968f";
const RULE = "#cdd6ce";
const SURFACE = "#ffffff";
const GROUND = "#edf0ec";

const MONO = '11px ui-monospace, "SF Mono", Menlo, monospace';
const TITLE = '600 13px system-ui, -apple-system, "Segoe UI", sans-serif';

export interface ExportCard {
  card: Card;
  view: VegaView | null;
}

/** Trim to what fits, with an ellipsis. Used for the title and the facts
 *  line, which are single-line by nature. */
function clip(ctx: CanvasRenderingContext2D, text: string, width: number): string {
  if (ctx.measureText(text).width <= width) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > width) {
    cut = cut.slice(0, -1);
  }
  return `${cut}…`;
}

/** The restatement wraps rather than truncating. It is the sentence that
 *  says what the numbers mean, so an export that cuts it off mid-clause
 *  carries a chart with no statement of its meaning — the exact thing the
 *  caption strip exists to prevent. */
function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const candidate = line ? `${line} ${word}` : word;
    if (ctx.measureText(candidate).width <= width) {
      line = candidate;
      continue;
    }
    if (line) lines.push(line);
    line = word;
    if (lines.length === MAX_LINES) break;
  }
  if (line && lines.length < MAX_LINES) lines.push(line);
  if (lines.length === MAX_LINES) {
    lines[MAX_LINES - 1] = clip(ctx, lines[MAX_LINES - 1], width);
  }
  return lines;
}

function freshness(card: Card): string {
  const r = card.render;
  const parts: string[] = [];
  if (typeof r?.row_count === "number") {
    parts.push(`${r.row_count} row${r.row_count === 1 ? "" : "s"}`);
  }
  if (r?.data_max_ts) parts.push(`through ${String(r.data_max_ts).slice(0, 10)}`);
  if (r?.fetched_at) {
    parts.push(`as of ${new Date(r.fetched_at).toISOString().slice(0, 16).replace("T", " ")}`);
  }
  return parts.join(" · ");
}

/**
 * Composite the board into one image.
 *
 * The caption strip is the point, not decoration. An exported board without
 * its restatement and its "data through" date is a picture of numbers with
 * nothing on it saying what they mean or how old they are — which is how
 * stale figures end up in a deck.
 */
export async function boardPng(title: string, entries: ExportCard[]): Promise<Blob> {
  const drawable = entries.filter((e) => e.view && e.card.render?.state === "ready");
  if (!drawable.length) throw new Error("Nothing on this dashboard can be exported yet.");

  const images = await Promise.all(
    drawable.map(async (e) => ({
      card: e.card,
      image: await loadImage(await chartPng(e.view as VegaView, SCALE)),
    })),
  );

  const cols = Math.max(...drawable.map((e) => e.card.layout.x + e.card.layout.w), COLS);
  const rows = Math.max(...drawable.map((e) => e.card.layout.y + e.card.layout.h));
  const width = cols * COL_W + PAD * 2;
  const height = rows * ROW_H + PAD * 2 + 44;

  const canvas = document.createElement("canvas");
  canvas.width = width * SCALE;
  canvas.height = height * SCALE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot render a canvas.");
  ctx.scale(SCALE, SCALE);

  ctx.fillStyle = GROUND;
  ctx.fillRect(0, 0, width, height);

  ctx.fillStyle = INK;
  ctx.font = TITLE;
  ctx.textBaseline = "top";
  ctx.fillText(title, PAD, PAD);
  ctx.font = MONO;
  ctx.fillStyle = FAINT;
  ctx.fillText(
    `exported ${new Date().toISOString().slice(0, 10)} · grounded in a curated layer`,
    PAD,
    PAD + 18,
  );

  for (const { card, image } of images) {
    const x = PAD + card.layout.x * COL_W;
    const y = PAD + 44 + card.layout.y * ROW_H;
    const w = card.layout.w * COL_W - 10;
    const h = card.layout.h * ROW_H - 10;

    ctx.fillStyle = SURFACE;
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = RULE;
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);

    ctx.save();
    ctx.beginPath();
    ctx.rect(x + 1, y + 1, w - 2, h - 2);
    ctx.clip();

    ctx.fillStyle = INK;
    ctx.font = TITLE;
    ctx.fillText(clip(ctx, card.title || "Untitled", w - 20), x + 10, y + 9);

    ctx.font = MONO;
    ctx.fillStyle = FAINT;
    let cursor = y + 28;
    for (const line of wrap(ctx, card.render?.restatement ?? "", w - 20)) {
      ctx.fillText(line, x + 10, cursor);
      cursor += LINE;
    }
    // Wraps too: a clipped "as of 2026-08-2…" states the freshness of the
    // numbers without actually saying when they are from.
    for (const line of wrap(ctx, freshness(card), w - 20)) {
      ctx.fillText(line, x + 10, cursor);
      cursor += LINE;
    }

    // Preserve the chart's aspect ratio inside whatever the caption leaves.
    const top = Math.max(cursor + 6, y + CAPTION_BASE);
    const slotW = w - 20;
    const slotH = y + h - top - 10;
    if (slotH > 20) {
      const ratio = Math.min(slotW / image.width, slotH / image.height);
      ctx.drawImage(image, x + 10, top, image.width * ratio, image.height * ratio);
    }
    ctx.restore();
  }

  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not encode the image."))), "image/png"),
  );
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("A chart could not be rasterised."));
    img.src = src;
  });
}
