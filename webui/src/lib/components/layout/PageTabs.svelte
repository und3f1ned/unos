<script lang="ts">
  import { Button, type IconComponent } from '$lib/components/ui';

  // The tabs of a page with tabs, System's and the Account page's (ADR-0428, ADR-0430): from xl
  // the groups are a 220 px column beside the tab, below it labelled rows of chips above it. A
  // group's label shows only when the page has more than one group. Each tab has a hash of its
  // own (`/system#network`, `/account#appearance`), which the page follows (tabs.ts).

  type PageTab = { id: string; icon: IconComponent; label: string };
  type PageTabGroup = { id: string; label: string; tabs: PageTab[] };

  type Props = {
    /** The id of the page's h1, which names the navigation. */
    labelledby: string;
    /** A prefix of the group labels' ids, unique on the page. */
    idPrefix: string;
    groups: PageTabGroup[];
    active: string;
    onselect: (id: string) => void;
  };

  let { labelledby, idPrefix, groups, active, onselect }: Props = $props();

  let labelled = $derived(groups.length > 1);
</script>

<nav aria-labelledby={labelledby} class="flex flex-col gap-2.5 min-w-0 xl:gap-[18px] xl:sticky xl:top-0">
  {#each groups as group (group.id)}
    <div class="min-w-0">
      {#if labelled}
        <p id="{idPrefix}-group-{group.id}" class="eyebrow mb-1.5 xl:mx-2.5">{group.label}</p>
      {/if}
      <ul
        class="flex flex-wrap gap-1.5 xl:flex-col xl:gap-0.5"
        aria-labelledby={labelled ? `${idPrefix}-group-${group.id}` : undefined}
      >
        {#each group.tabs as tab (tab.id)}
          {@const current = active === tab.id}
          <li class="min-w-0">
            <Button variant="plain"
              onclick={() => onselect(tab.id)}
              aria-current={current ? "true" : undefined}
              class="relative flex items-center gap-2 h-8 px-3 rounded border text-[13px] transition-colors xl:w-full xl:h-9 xl:px-2.5 xl:border-0 xl:text-sm {current
                ? 'bg-brand/20 border-brand-ring text-content font-medium xl:before:absolute xl:before:left-0 xl:before:top-2 xl:before:bottom-2 xl:before:w-[3px] xl:before:rounded-r-[3px] xl:before:bg-brand-ring'
                : 'bg-surface-elevated border-border text-content-secondary hover:bg-surface-overlay hover:text-content xl:bg-transparent'}"
            >
              <tab.icon class="w-4 h-4 shrink-0 xl:w-[18px] xl:h-[18px] {current ? 'text-brand-ring' : 'text-content-muted'}" aria-hidden="true" />
              <span class="truncate">{tab.label}</span>
            </Button>
          </li>
        {/each}
      </ul>
    </div>
  {/each}
</nav>
