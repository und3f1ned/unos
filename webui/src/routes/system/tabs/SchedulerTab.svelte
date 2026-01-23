<script lang="ts">
  import { onMount } from "svelte";
  import { getTasks, type ScheduledTask } from "$lib/api/scheduler";
  import { Calendar, Clock, Play, Pause, RefreshCw } from "lucide-svelte";

  let tasks: ScheduledTask[] = [];
  let loading = true;

  onMount(() => loadTasks());

  async function loadTasks() {
    loading = true;
    try {
      const result = await getTasks();
      tasks = Array.isArray(result) ? result : [];
    } catch (e) {
      console.error(e);
      tasks = [];
    }
    loading = false;
  }
</script>

<div class="space-y-4">
  <div class="flex items-center justify-between">
    <h2 class="text-xl font-bold">Scheduled Tasks</h2>
    <button on:click={loadTasks} class="p-2 bg-slate-700 rounded-lg hover:bg-slate-600">
      <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
    </button>
  </div>

  <div class="bg-slate-800 rounded-lg p-4">
    {#if loading}
      <div class="flex justify-center py-8">
        <RefreshCw class="w-6 h-6 animate-spin text-blue-500" />
      </div>
    {:else if tasks.length === 0}
      <div class="text-center py-8 text-slate-400">
        <Calendar class="w-10 h-10 mx-auto mb-2 opacity-50" />
        <p>No scheduled tasks</p>
      </div>
    {:else}
      <div class="space-y-2">
        {#each tasks as task}
          <div class="flex items-center justify-between p-3 bg-slate-700 rounded-lg">
            <div class="flex items-center gap-3">
              {#if task.enabled}
                <Play class="w-5 h-5 text-green-400" />
              {:else}
                <Pause class="w-5 h-5 text-slate-500" />
              {/if}
              <div>
                <p class="font-medium text-sm">{task.name}</p>
                <p class="text-xs text-slate-400">{task.task_type} - {task.frequency}</p>
              </div>
            </div>
            {#if task.next_run}
              <span class="text-xs text-slate-500 flex items-center gap-1">
                <Clock class="w-3 h-3" />
                {new Date(task.next_run).toLocaleString()}
              </span>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
