import type { ComponentType } from 'svelte';
import type { Icon } from 'lucide-svelte';

// An icon of lucide-svelte, which a component draws: `icon={Trash2}`. lucide-svelte 0.x ships
// class components of Svelte 4, typed as ComponentType (ADR-0425).
export type IconComponent = ComponentType<Icon>;
