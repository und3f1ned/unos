// How the command palette narrows its choices to what is typed (ADR-0388), without Svelte or
// imports, so that supervisor/tests/test_command_palette.py runs it with node.

/** A choice of the palette: its names in every language and the other words it is known by. */
export interface Findable {
	readonly names: readonly string[];
	readonly keywords: readonly string[];
}

/** Lower case, ё as е, and single spaces: what the search compares. */
export function fold(text: string): string {
	return text.normalize('NFC').toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ').trim();
}

/** The words of a folded text: its runs of letters and digits. */
function wordsOf(folded: string): string[] {
	return folded.split(/[^\p{L}\p{N}]+/u).filter((word) => word !== '');
}

/**
 * How well `item` matches `query`, the lower the better, or null when it does not:
 * 0, a name starts with the query; 1, each word of the query starts a word of one name;
 * 2, a name holds the query; 3, each word of the query starts a word of the names or keywords;
 * 4, the names and keywords hold each word of the query. An empty query matches everything at 0.
 */
export function rank(item: Findable, query: string): number | null {
	const wanted = fold(query);
	if (wanted === '') return 0;
	const queryWords = wordsOf(wanted);
	const names = item.names.map(fold);
	const everything = [...names, ...item.keywords.map(fold)];
	const startsAWord = (words: string[]) => (word: string) => words.some((other) => other.startsWith(word));

	if (names.some((name) => name.startsWith(wanted))) return 0;
	if (queryWords.length > 0 && names.some((name) => queryWords.every(startsAWord(wordsOf(name))))) return 1;
	if (names.some((name) => name.includes(wanted))) return 2;
	if (queryWords.length === 0) return null;
	const allWords = everything.flatMap(wordsOf);
	if (queryWords.every(startsAWord(allWords))) return 3;
	if (queryWords.every((word) => everything.some((text) => text.includes(word)))) return 4;
	return null;
}

/** The items that match `query`, the best first, and in their own order where they match alike. */
export function narrow<T extends Findable>(items: readonly T[], query: string): T[] {
	return items
		.map((item, index) => ({ item, index, rank: rank(item, query) }))
		.filter((entry): entry is { item: T; index: number; rank: number } => entry.rank !== null)
		.sort((a, b) => a.rank - b.rank || a.index - b.index)
		.map((entry) => entry.item);
}
