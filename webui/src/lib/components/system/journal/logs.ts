// Helpers of System > Logs: its texts come from src/lib/i18n/catalog/logs.ts (ADR-0287).
import type { Translate } from "$lib/i18n";

/** The text of `value` by its key in `keys`, in the language of the page; else `fallback`. */
export function textOf(t: Translate, keys: Record<string, string>, value: string, fallback: string = value): string {
  return Object.prototype.hasOwnProperty.call(keys, value) ? t(keys[value]) : fallback;
}

/** The badge of an entry's level: the errors and worse red, a warning amber, a notice blue. */
export function priorityVariant(p: string): "error" | "warning" | "success" | "info" | "neutral" {
  switch (p) {
    case "emerg":
    case "alert":
    case "crit":
    case "err":
      return "error";
    case "warning":
      return "warning";
    case "notice":
      return "info";
    case "info":
      return "neutral";
    case "debug":
      return "neutral";
    default:
      return "neutral";
  }
}

// Split a log message around case-insensitive matches of the search term.
// The parts are rendered as text, never as HTML: log lines are attacker-
// controllable (usernames, request paths, container output).
export function splitByMatch(text: string, term: string): { text: string; match: boolean }[] {
  if (!term) return [{ text, match: false }];
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // With one capture group, split() puts the matches at the odd indexes.
  return text
    .split(new RegExp(`(${escaped})`, "gi"))
    .map((part, i) => ({ text: part, match: i % 2 === 1 }))
    .filter((part) => part.text !== "");
}
