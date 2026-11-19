// The choices of the command palette (ADR-0393): the pages and actions of each role, by the keys
// of their names in the catalogs. What changes the NAS is never done by the palette: it opens the
// page's own dialog, which asks first.
import {
	Archive,
	ArrowUpCircle,
	Bell,
	Calendar,
	Container,
	Database,
	FileText,
	FolderOpen,
	Gauge,
	Globe,
	HardDrive,
	Heart,
	Languages,
	LayoutDashboard,
	LogOut,
	Moon,
	Network,
	Package,
	Power,
	RotateCcw,
	ScrollText,
	Settings,
	Share2,
	Shield,
	Trash2,
	UserCog,
	UserPlus,
	Users,
	Wrench
} from 'lucide-svelte';
import type { IconComponent } from '$lib/components/ui';
import { LANGUAGES, translateIn, type Locale } from '$lib/i18n';
import type { PaletteDialog } from '$lib/stores/palette';

/** What a choice does: open a page, open a page's dialog, set the language, or log out. */
export type Run =
	| { go: string; dialog?: PaletteDialog }
	| { language: Locale }
	| { logout: true };

export interface Choice {
	id: string;
	nameKey: string;
	/** The other words the choice is known by, in the catalog of the palette. */
	keywordsKey?: string;
	kindKey: string;
	icon: IconComponent;
	run: Run;
}

const PAGE = 'palette.kind.page';
const SYSTEM = 'layout.nav.system';
const ACTION = 'palette.kind.action';

// The pages of the sidebar (layout/NavMenu.svelte), with the trash of Files, and the tabs of the
// System page (src/routes/system/+page.svelte), in their order: webui/tests/command-palette.spec.ts
// compares them.
export const ADMIN_PAGES: Choice[] = [
	{ id: 'dashboard', nameKey: 'layout.nav.dashboard', kindKey: PAGE, icon: LayoutDashboard, run: { go: '/' } },
	{
		id: 'storage',
		nameKey: 'layout.nav.storage',
		keywordsKey: 'palette.keywords.storage',
		kindKey: PAGE,
		icon: HardDrive,
		run: { go: '/storage' }
	},
	{ id: 'snapshots', nameKey: 'layout.nav.snapshots', kindKey: PAGE, icon: Archive, run: { go: '/snapshots' } },
	{
		id: 'shares',
		nameKey: 'layout.nav.shares',
		keywordsKey: 'palette.keywords.shares',
		kindKey: PAGE,
		icon: Share2,
		run: { go: '/shares' }
	},
	{ id: 'files', nameKey: 'layout.nav.files', kindKey: PAGE, icon: FolderOpen, run: { go: '/files' } },
	{ id: 'trash', nameKey: 'files.trash.title', kindKey: PAGE, icon: Trash2, run: { go: '/files/trash' } },
	{
		id: 'containers',
		nameKey: 'layout.nav.containers',
		keywordsKey: 'palette.keywords.containers',
		kindKey: PAGE,
		icon: Container,
		run: { go: '/containers' }
	},
	{
		id: 'apps',
		nameKey: 'layout.nav.apps',
		keywordsKey: 'palette.keywords.apps',
		kindKey: PAGE,
		icon: Package,
		run: { go: '/apps' }
	},
	{
		id: 'users',
		nameKey: 'layout.nav.users',
		keywordsKey: 'palette.keywords.users',
		kindKey: PAGE,
		icon: Users,
		run: { go: '/users' }
	},
	{ id: 'audit', nameKey: 'layout.nav.audit', kindKey: PAGE, icon: ScrollText, run: { go: '/audit' } },
	{ id: 'system', nameKey: 'layout.nav.system', kindKey: PAGE, icon: Wrench, run: { go: '/system' } },
	{
		id: 'general',
		nameKey: 'system.tabs.general',
		keywordsKey: 'palette.keywords.general',
		kindKey: SYSTEM,
		icon: Settings,
		run: { go: '/system#general' }
	},
	{
		id: 'network',
		nameKey: 'system.tabs.network',
		keywordsKey: 'palette.keywords.network',
		kindKey: SYSTEM,
		icon: Network,
		run: { go: '/system#network' }
	},
	{
		id: 'remote',
		nameKey: 'remote.tab',
		keywordsKey: 'palette.keywords.remote',
		kindKey: SYSTEM,
		icon: Globe,
		run: { go: '/system#remote' }
	},
	{
		id: 'security',
		nameKey: 'system.tabs.security',
		keywordsKey: 'palette.keywords.security',
		kindKey: SYSTEM,
		icon: Shield,
		run: { go: '/system#security' }
	},
	{
		id: 'monitor',
		nameKey: 'system.tabs.monitor',
		keywordsKey: 'palette.keywords.monitor',
		kindKey: SYSTEM,
		icon: Gauge,
		run: { go: '/system#monitor' }
	},
	{ id: 'logs', nameKey: 'system.tabs.logs', kindKey: SYSTEM, icon: FileText, run: { go: '/system#logs' } },
	{
		id: 'health',
		nameKey: 'system.tabs.health',
		keywordsKey: 'palette.keywords.health',
		kindKey: SYSTEM,
		icon: Heart,
		run: { go: '/system#health' }
	},
	{
		id: 'notifications',
		nameKey: 'system.tabs.notifications',
		keywordsKey: 'palette.keywords.notifications',
		kindKey: SYSTEM,
		icon: Bell,
		run: { go: '/system#notifications' }
	},
	{
		id: 'updates',
		nameKey: 'system.tabs.updates',
		keywordsKey: 'palette.keywords.updates',
		kindKey: SYSTEM,
		icon: ArrowUpCircle,
		run: { go: '/system#updates' }
	},
	{ id: 'backup', nameKey: 'system.tabs.backup', kindKey: SYSTEM, icon: Database, run: { go: '/system#backup' } },
	{
		id: 'scheduler',
		nameKey: 'system.tabs.scheduler',
		keywordsKey: 'palette.keywords.scheduler',
		kindKey: SYSTEM,
		icon: Calendar,
		run: { go: '/system#scheduler' }
	},
	{
		id: 'power',
		nameKey: 'system.tabs.power',
		keywordsKey: 'palette.keywords.power',
		kindKey: SYSTEM,
		icon: Power,
		run: { go: '/system#power' }
	}
];

// A regular user's pages: those of the sidebar (ADR-0030).
export const USER_PAGES: Choice[] = [
	{ id: 'dashboard', nameKey: 'layout.nav.dashboard', kindKey: PAGE, icon: LayoutDashboard, run: { go: '/' } },
	{
		id: 'account',
		nameKey: 'layout.nav.account',
		keywordsKey: 'palette.keywords.account',
		kindKey: PAGE,
		icon: UserCog,
		run: { go: '/system#security' }
	}
];

// What changes the NAS is never done here: the palette opens the page's own dialog, which asks
// and acts only once the user confirms it there.
export const ADMIN_ACTIONS: Choice[] = [
	{
		id: 'add-user',
		nameKey: 'palette.action.addUser',
		keywordsKey: 'palette.keywords.addUser',
		kindKey: ACTION,
		icon: UserPlus,
		run: { go: '/users', dialog: 'add-user' }
	},
	{
		id: 'reboot',
		nameKey: 'palette.action.reboot',
		keywordsKey: 'palette.keywords.reboot',
		kindKey: ACTION,
		icon: RotateCcw,
		run: { go: '/system#power', dialog: 'reboot' }
	},
	{
		id: 'shutdown',
		nameKey: 'palette.action.shutdown',
		keywordsKey: 'palette.keywords.shutdown',
		kindKey: ACTION,
		icon: Power,
		run: { go: '/system#power', dialog: 'shutdown' }
	},
	{
		id: 'suspend',
		nameKey: 'palette.action.suspend',
		keywordsKey: 'palette.keywords.suspend',
		kindKey: ACTION,
		icon: Moon,
		run: { go: '/system#power', dialog: 'suspend' }
	}
];

// The actions that the palette runs itself: they change nothing on the NAS. The language offered
// is the one the page is not in.
const TO_LANGUAGE: Record<Locale, string> = {
	en: 'palette.action.toEnglish',
	ru: 'palette.action.toRussian'
};

export function ownActions(current: Locale): Choice[] {
	return [
		...LANGUAGES.filter((language) => language.code !== current).map((language) => ({
			id: `language-${language.code}`,
			nameKey: TO_LANGUAGE[language.code],
			kindKey: ACTION,
			icon: Languages,
			run: { language: language.code }
		})),
		{ id: 'logout', nameKey: 'layout.logout', kindKey: ACTION, icon: LogOut, run: { logout: true } }
	];
}

/** The text of `key` in every language of the web UI. */
export function inEveryLanguage(key: string | undefined): string[] {
	return key ? LANGUAGES.map((language) => translateIn(language.code, key)) : [];
}
