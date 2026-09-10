/** Hand-rolled rather than pulled from d3-dsv. d3-dsv is only present as a
 *  transitive dependency of vega-loader; importing it directly would make
 *  this file break silently the day that tree changes. */

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = value instanceof Date ? value.toISOString() : String(value);
  // Quote when the value could otherwise be read as structure, and double
  // any quote inside it, per RFC 4180.
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Column order comes from the rows themselves: every key seen, first
 *  appearance first, so the CSV matches the shape the card was built from
 *  even when later rows omit a null column. */
export function toCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!columns.includes(key)) columns.push(key);
    }
  }
  const lines = [columns.map(cell).join(",")];
  for (const row of rows) {
    lines.push(columns.map((c) => cell(row[c])).join(","));
  }
  // Trailing newline: without it some tools treat the last row as partial.
  return `${lines.join("\r\n")}\r\n`;
}

export function csvBlob(rows: Record<string, unknown>[]): Blob {
  // The BOM is what makes Excel open UTF-8 as UTF-8 rather than as the
  // system codepage, which is how region names arrive mangled.
  return new Blob([`﻿${toCsv(rows)}`], {
    type: "text/csv;charset=utf-8",
  });
}
