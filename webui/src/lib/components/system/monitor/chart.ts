// The charts of System > Resource Monitor (ADR-0399): the points of the history and of the stats
// stream, and the SVG paths that draw a series of them.

/** A point of the history: a stats message, at the browser's time. */
export interface Point {
  // The browser's time of the point, in milliseconds.
  at: number;
  cpu: number;
  memory: number;
  netRecv: number;
  netSent: number;
  diskRead: number;
  diskWrite: number;
}

/** A series of a chart: the value it draws of each point. */
export type Series = (point: Point) => number;

// Two points further apart than this have no data between them: the chart's line breaks there.
const GAP_MS = 30 * 1000;

export const cpuOf: Series = (point) => point.cpu;
export const memoryOf: Series = (point) => point.memory;
export const recvOf: Series = (point) => point.netRecv;
export const sentOf: Series = (point) => point.netSent;
export const readOf: Series = (point) => point.diskRead;
export const writeOf: Series = (point) => point.diskWrite;

/** The highest value of a series, 0 for none. */
export function peak(series: Point[], value: Series): number {
  return series.reduce((highest, point) => Math.max(highest, value(point)), 0);
}

// The commands of an SVG path over a chart of 100 x 40, from `from` over `span` milliseconds, with
// values from 0 to max; a gap breaks the line.
export function path(series: Point[], value: Series, max: number, from: number, span: number): string {
  let previous: number | null = null;
  const commands: string[] = [];
  for (const point of series) {
    const x = (((point.at - from) / span) * 100).toFixed(2);
    const y = (40 - (Math.min(value(point), max) / Math.max(max, 1e-9)) * 40).toFixed(2);
    const move = previous === null || point.at - previous > GAP_MS;
    commands.push((move ? "M " : "L ") + x + " " + y);
    previous = point.at;
  }
  return commands.join(" ");
}
