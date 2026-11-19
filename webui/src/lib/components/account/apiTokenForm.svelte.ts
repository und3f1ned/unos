// The form of a new API token (ADR-0392), which the card creates and its dialog fills. It starts at
// the least that a person can choose: read only.
import { TOKEN_AREAS, WARNED_AREAS, type TokenArea } from '$lib/api/auth';

export class ApiTokenForm {
	name = $state('');
	/** Days until it expires, of the dialog's choices; '' never expires. */
	lifetime = $state('');
	readOnly = $state(true);
	everyArea = $state(true);
	chosen = $state<Partial<Record<TokenArea, boolean>>>({});

	chosenAreas = $derived(TOKEN_AREAS.filter((area) => this.chosen[area]));
	canCreate = $derived(this.name.trim().length > 0 && (this.everyArea || this.chosenAreas.length > 0));
	// Change access to chosen areas whose changes reach beyond them (WARNED_AREAS). Change access to
	// every area is a full token, as the choice says.
	warned = $derived(
		!this.readOnly && !this.everyArea && this.chosenAreas.some((area) => WARNED_AREAS.includes(area))
	);

	/** Back to the first choices, for the next token. */
	reset() {
		this.name = '';
		this.lifetime = '';
		this.readOnly = true;
		this.everyArea = true;
		this.chosen = {};
	}
}
