// The words of the host's firewall (ADR-0416) that the card, its rules and its dialogs share: the
// labels of the values that the server answers as words (FirewallRule and FirewallStatus of
// $lib/api/security), keys of the catalog, by value.
import type { FirewallAction, FirewallProtocol, FirewallRule } from '$lib/api/security';
import type { Translate } from '$lib/i18n';

/** The page's $t: a key's text in the language of the page. */

export const RULE_ACTIONS: Record<string, string> = {
	allow: 'firewall.allow',
	deny: 'firewall.deny',
	reject: 'firewall.reject',
	limit: 'firewall.limit'
};
export const RULE_DIRECTIONS: Record<string, string> = { in: 'firewall.in', out: 'firewall.out' };
export const RULE_PROTOCOLS: Record<string, string> = {
	tcp: 'firewall.tcp',
	udp: 'firewall.udp',
	any: 'firewall.anyProtocol'
};
export const POLICIES: Record<string, string> = {
	allow: 'firewall.policyAllow',
	deny: 'firewall.policyDeny',
	reject: 'firewall.policyReject'
};

/** The label of `value` in the language of `t`; a value without one as it comes. */
export function labelOf(labels: Record<string, string>, value: string, t: Translate): string {
	return Object.prototype.hasOwnProperty.call(labels, value) ? t(labels[value]) : value;
}

/** A rule's direction, protocol, port and addresses: "IN | TCP | Port: 22 | From: 192.168.1.0/24". */
export function ruleLine(rule: FirewallRule, t: Translate): string {
	const parts = [
		labelOf(RULE_DIRECTIONS, rule.direction, t),
		labelOf(RULE_PROTOCOLS, rule.protocol, t),
		t('firewall.port', { port: rule.port || t('firewall.any') })
	];
	if (rule.source) parts.push(t('firewall.from', { source: rule.source }));
	if (rule.destination) parts.push(t('firewall.to', { destination: rule.destination }));
	if (rule.interface) parts.push(t('firewall.on', { interface: rule.interface }));
	// i18n-ignore: a separator
	return parts.join(' | ');
}

/** The fields of the dialog of a new rule. */
export type NewRule = { action: FirewallAction; protocol: FirewallProtocol; port: string; source: string; comment: string };

export function emptyRule(): NewRule {
	return { action: 'allow', protocol: 'tcp', port: '', source: '', comment: '' };
}
