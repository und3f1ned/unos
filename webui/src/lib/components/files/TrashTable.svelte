<script lang="ts">
  import { formatSize, type TrashItem } from "$lib/api/files";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDate, formatDateTime } from "$lib/time";
  import { trashItemIcon } from "./icons";

  type Props = {
    items: TrashItem[];
    // The paths of the selected items.
    selected: ReadonlySet<string>;
    // Sizes in the units of the page's language.
    units: readonly string[];
    // An item's checkbox was ticked or cleared.
    ontoggle: (path: string) => void;
  };

  let { items, selected, units, ontoggle }: Props = $props();

  // The texts that the functions below make follow a change of the language: the markup that
  // calls them reads $t and $intlLocale through them.

  // The server gives UTC times with their offset; the browser shows them in its own time zone,
  // in the language of the page.
  function formatTime(value: string, dateOnly = false): string {
    return dateOnly ? formatDate(value, $intlLocale) : formatDateTime(value, $intlLocale);
  }

  // The folder an item was deleted from.
  function folderOf(item: TrashItem): string {
    if (item.original_path === null) return $t("files.trash.notKnown");
    const cut = item.original_path.lastIndexOf("/");
    return cut > 0 ? item.original_path.slice(0, cut) : "/";
  }

  // What a screen reader says for an item's checkbox: items of the same name
  // differ in where and when they were deleted (ADR-0197).
  function checkboxLabel(item: TrashItem): string {
    const time = formatTime(item.deleted);
    return item.original_path === null
      ? $t("files.trash.selectUnknown", { name: item.name, time })
      : $t("files.trash.select", { name: item.name, folder: folderOf(item), time });
  }
</script>

<div class="table-frame">
  <table class="table" aria-label={$t("files.trash.items")}>
    <thead class="table-header">
      <tr>
        <th class="w-8"><span class="sr-only">{$t("files.trash.column.selected")}</span></th>
        <th>{$t("files.column.name")}</th>
        <th>{$t("files.trash.column.from")}</th>
        <th>{$t("files.trash.column.deleted")}</th>
        <th>{$t("files.trash.column.keptUntil")}</th>
        <th>{$t("files.column.size")}</th>
      </tr>
    </thead>
    <tbody>
      {#each items as item (item.path)}
        {@const Icon = trashItemIcon(item)}
        <tr class="table-row align-top">
          <td>
            <input
              type="checkbox"
              aria-label={checkboxLabel(item)}
              checked={selected.has(item.path)}
              onchange={() => ontoggle(item.path)}
              class="w-4 h-4 rounded bg-surface-overlay border-border"
            />
          </td>
          <td>
            <div class="flex items-center gap-2 min-w-0">
              <Icon class="w-4 h-4 flex-shrink-0 text-content-secondary" />
              <span class="break-all">{item.name}</span>
            </div>
          </td>
          <td class="mono-xs break-all">{folderOf(item)}</td>
          <td class="whitespace-nowrap">{formatTime(item.deleted)}</td>
          <td class="whitespace-nowrap">{formatTime(item.expires, true)}</td>
          <td class="whitespace-nowrap">{item.type === "directory" ? "-" : formatSize(item.size, units)}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
