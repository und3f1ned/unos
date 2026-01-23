<script lang="ts">
  import {
    FolderOpen,
    Share2,
    Download,
    Upload,
    Terminal,
    Settings,
    Power,
    RefreshCw
  } from "lucide-svelte";
  import { goto } from "$app/navigation";

  export let onAction: ((action: string) => void) | undefined = undefined;

  const actions = [
    { id: "files", icon: FolderOpen, label: "File Browser", href: "/files", color: "text-blue-400" },
    { id: "shares", icon: Share2, label: "Manage Shares", href: "/shares", color: "text-green-400" },
    { id: "apps", icon: Download, label: "App Store", href: "/apps", color: "text-purple-400" },
    { id: "settings", icon: Settings, label: "Settings", href: "/settings", color: "text-slate-400" },
  ];

  function handleClick(action: typeof actions[0]) {
    if (onAction) {
      onAction(action.id);
    } else if (action.href) {
      goto(action.href);
    }
  }
</script>

<div class="bg-slate-800 rounded-lg p-4">
  <h3 class="font-semibold mb-4">Quick Actions</h3>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
    {#each actions as action}
      <button
        on:click={() => handleClick(action)}
        class="flex flex-col items-center gap-2 p-3 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
      >
        <svelte:component this={action.icon} class="w-6 h-6 {action.color}" />
        <span class="text-xs text-center">{action.label}</span>
      </button>
    {/each}
  </div>
</div>
