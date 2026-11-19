import { tick } from 'svelte';
import { streamContainerLogs, type ContainerLogStream } from '$lib/api/containers';

// The log view keeps this much of the newest text, cut at a line break (ADR-0255).
const MAX_LOG_CHARS = 500_000;
// Closer to the end than this (pixels), the view follows new lines.
const FOLLOW_SLACK_PX = 24;

export type LogState = 'idle' | 'connecting' | 'live' | 'ended' | 'lost' | 'forbidden';

/**
 * The log of one container as its WebSocket streams it (streamContainerLogs, ADR-0255): the
 * drawer follows the container whose Logs tab it shows, and the tab draws the text.
 */
export class ContainerLog {
	text = $state('');
	error = $state('');
	state = $state<LogState>('idle');
	/** The element that shows the text (the tab binds it): it follows new lines while at its end. */
	view: HTMLPreElement | undefined = undefined;
	#stream: ContainerLogStream | null = null;
	#streamedId: string | null = null;

	/** Stream the log of `id`, or none for null; the same id goes on as it is. */
	follow(id: string | null) {
		if (id === this.#streamedId) return;
		this.stop();
		if (id !== null) this.#start(id);
	}

	/** The stream ended or was lost: the same container's log from the start. */
	reconnect() {
		const id = this.#streamedId;
		this.stop();
		if (id !== null) this.#start(id);
	}

	stop() {
		this.#stream?.close();
		this.#stream = null;
		this.#streamedId = null;
		this.state = 'idle';
	}

	#start(id: string) {
		this.#streamedId = id;
		this.text = '';
		this.error = '';
		this.state = 'connecting';
		this.#stream = streamContainerLogs(id, {
			onLive: () => (this.state = 'live'),
			onLog: (data) => this.#append(data),
			onError: (message) => (this.error = message),
			onEnd: (reason) => {
				this.#stream = null;
				this.state = reason;
			}
		});
	}

	async #append(data: string) {
		const view = this.view;
		const atEnd = !view || view.scrollHeight - view.scrollTop - view.clientHeight < FOLLOW_SLACK_PX;
		let next = this.text + data;
		if (next.length > MAX_LOG_CHARS) {
			// Search from the last character that must go: the first line break from there
			// ends the last dropped line, so what follows is the most whole lines that fit.
			const cut = next.indexOf('\n', next.length - MAX_LOG_CHARS - 1);
			next = cut === -1 ? next.slice(-MAX_LOG_CHARS) : next.slice(cut + 1);
		}
		this.text = next;
		if (atEnd) {
			await tick();
			this.view?.scrollTo({ top: this.view.scrollHeight });
		}
	}
}
