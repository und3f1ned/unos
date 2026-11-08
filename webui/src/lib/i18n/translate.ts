// The lookup behind $t (ADR-0273), without Svelte or SvelteKit, so that
// supervisor/tests/test_i18n.py runs it with node.

/** The languages of the web UI. English is the fallback of every text. */
export const LOCALES = ['en', 'ru'] as const;
export type Locale = (typeof LOCALES)[number];

/** A text whose words follow a count: one form per plural category of the language. */
export type PluralForms = Partial<Record<Intl.LDMLPluralRule, string>>;
export type Message = string | PluralForms;
export type Messages = Record<string, Message>;
export type Catalogs = Record<Locale, Messages>;
/** The values of a text's {name} placeholders; n also picks the plural form. */
export type Params = Record<string, string | number>;

/**
 * The Russian half of a catalog: the keys of the English half, each a text where English has a
 * text and plural forms where English has them. svelte-check then fails for a missing or an
 * extra key, and scripts/i18n-check.mjs checks the forms and the placeholders.
 */
export type Translation<T> = { [K in keyof T]: T[K] extends string ? string : PluralForms };

const own = (object: object, key: string) => Object.prototype.hasOwnProperty.call(object, key);

export function isLocale(value: unknown): value is Locale {
	return LOCALES.includes(value as Locale);
}

const rules = new Map<Locale, Intl.PluralRules>();

/** The form of `forms` that the count `n` takes in `locale`, by the CLDR rules of Intl.PluralRules. */
export function pluralForm(locale: Locale, n: number, forms: PluralForms): string {
	let localeRules = rules.get(locale);
	if (!localeRules) {
		localeRules = new Intl.PluralRules(locale);
		rules.set(locale, localeRules);
	}
	return forms[localeRules.select(n)] ?? forms.other ?? forms.many ?? '';
}

/** `text` with each {name} replaced by params[name]; a placeholder without a value stays as it is. */
export function interpolate(text: string, params: Params = {}): string {
	return text.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
		own(params, name) ? String(params[name]) : placeholder
	);
}

/**
 * The text of `key` in `locale`, else in English, else the key itself, with the placeholders
 * filled in from `params`. A text with plural forms takes the form of params.n.
 */
export function translate(catalogs: Catalogs, locale: Locale, key: string, params: Params = {}): string {
	let language: Locale = locale;
	if (!own(catalogs[language], key)) language = 'en';
	if (!own(catalogs[language], key)) return key;
	const message = catalogs[language][key];
	const text = typeof message === 'string' ? message : pluralForm(language, Number(params.n), message);
	return interpolate(text, params);
}
