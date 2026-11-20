// The passwords that the Users page sets.

/** The shortest password that a form sends. */
export const MIN_PASSWORD_LENGTH = 8;

/** A password long enough, and its confirmation the same: a form can send it. */
export function passwordComplete(password: string, confirmation: string): boolean {
	return password.length >= MIN_PASSWORD_LENGTH && confirmation === password;
}
