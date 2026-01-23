<script lang="ts">
  import { rebootSystem, shutdownSystem } from "$lib/api/power";
  import { Power, RotateCcw, AlertTriangle } from "lucide-svelte";

  let showConfirm = false;
  let action = "";
  let executing = false;

  function confirmAction(a: string) {
    action = a;
    showConfirm = true;
  }

  async function executeAction() {
    executing = true;
    try {
      if (action === "reboot") await rebootSystem();
      else if (action === "shutdown") await shutdownSystem();
    } catch (e) {
      console.error(e);
    }
    executing = false;
    showConfirm = false;
  }
</script>

<div class="space-y-4">
  <h2 class="text-xl font-bold">Power Management</h2>

  <div class="bg-slate-800 rounded-lg p-6">
    <div class="flex items-center gap-4">
      <button
        on:click={() => confirmAction('reboot')}
        class="flex items-center gap-2 px-6 py-3 bg-yellow-600 hover:bg-yellow-500 rounded-lg transition-colors"
      >
        <RotateCcw class="w-5 h-5" />
        Reboot
      </button>
      <button
        on:click={() => confirmAction('shutdown')}
        class="flex items-center gap-2 px-6 py-3 bg-red-600 hover:bg-red-500 rounded-lg transition-colors"
      >
        <Power class="w-5 h-5" />
        Shutdown
      </button>
    </div>
    <p class="text-sm text-slate-400 mt-4">
      Warning: These actions will immediately affect the system.
    </p>
  </div>
</div>

{#if showConfirm}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 max-w-sm">
      <div class="flex items-center gap-3 mb-4">
        <AlertTriangle class="w-8 h-8 text-yellow-500" />
        <h3 class="text-lg font-semibold">Confirm {action}</h3>
      </div>
      <p class="text-slate-400 mb-4">Are you sure you want to {action} the system?</p>
      <div class="flex justify-end gap-3">
        <button
          on:click={() => showConfirm = false}
          class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
          disabled={executing}
        >
          Cancel
        </button>
        <button
          on:click={executeAction}
          class="px-4 py-2 bg-red-600 hover:bg-red-500 rounded-lg capitalize transition-colors"
          disabled={executing}
        >
          {executing ? 'Processing...' : action}
        </button>
      </div>
    </div>
  </div>
{/if}
