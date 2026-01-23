<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getContainers,
    startContainer,
    stopContainer,
    restartContainer,
    createContainer,
    removeContainer,
    type Container,
    type ContainerCreate
  } from '$lib/api/containers';
  import { Container as ContainerIcon, Play, Square, RotateCw, Trash2, Plus, X } from 'lucide-svelte';

  let containers: Container[] = [];
  let loading = true;
  let error = '';
  let showModal = false;
  let submitting = false;

  // Form state
  let formData: ContainerCreate = {
    name: '',
    image: '',
    ports: [],
    environment: {},
    restart_policy: 'unless-stopped'
  };
  let portMapping = { host: '', container: '', protocol: 'tcp' };
  let envVar = { key: '', value: '' };
  let portsList: Array<{ host: string; container: string; protocol: string }> = [];
  let envList: Array<{ key: string; value: string }> = [];

  onMount(async () => {
    await loadData();
  });

  async function loadData() {
    loading = true;
    try {
      containers = await getContainers();
    } catch (e: any) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  function openModal() {
    formData = {
      name: '',
      image: '',
      ports: [],
      environment: {},
      restart_policy: 'unless-stopped'
    };
    portsList = [];
    envList = [];
    portMapping = { host: '', container: '', protocol: 'tcp' };
    envVar = { key: '', value: '' };
    showModal = true;
  }

  function closeModal() {
    showModal = false;
  }

  function addPort() {
    if (portMapping.host && portMapping.container) {
      portsList = [...portsList, { ...portMapping }];
      portMapping = { host: '', container: '', protocol: 'tcp' };
    }
  }

  function removePort(index: number) {
    portsList = portsList.filter((_, i) => i !== index);
  }

  function addEnvVar() {
    if (envVar.key) {
      envList = [...envList, { ...envVar }];
      envVar = { key: '', value: '' };
    }
  }

  function removeEnvVar(index: number) {
    envList = envList.filter((_, i) => i !== index);
  }

  async function handleSubmit() {
    if (!formData.name || !formData.image) {
      error = 'Name and image are required';
      return;
    }

    submitting = true;
    error = '';

    try {
      const config: ContainerCreate = {
        name: formData.name,
        image: formData.image,
        restart_policy: formData.restart_policy,
        ports: portsList.map(p => ({
          host_port: parseInt(p.host),
          container_port: parseInt(p.container),
          protocol: p.protocol
        })),
        environment: envList.reduce((acc, e) => ({ ...acc, [e.key]: e.value }), {})
      };

      await createContainer(config);
      closeModal();
      await loadData();
    } catch (e: any) {
      error = e.message;
    } finally {
      submitting = false;
    }
  }

  async function handleRemove(id: string, name: string) {
    if (!confirm(`Remove container "${name}"?`)) return;

    try {
      await removeContainer(id, true);
      await loadData();
    } catch (e: any) {
      error = e.message;
    }
  }

  async function handleStart(id: string) {
    try {
      await startContainer(id);
      await loadData();
    } catch (e: any) {
      error = e.message;
    }
  }

  async function handleStop(id: string) {
    try {
      await stopContainer(id);
      await loadData();
    } catch (e: any) {
      error = e.message;
    }
  }

  async function handleRestart(id: string) {
    try {
      await restartContainer(id);
      await loadData();
    } catch (e: any) {
      error = e.message;
    }
  }

  function getStateColor(state: string): string {
    switch (state) {
      case 'running':
        return 'bg-green-500/20 text-green-400';
      case 'exited':
        return 'bg-red-500/20 text-red-400';
      case 'paused':
        return 'bg-yellow-500/20 text-yellow-400';
      default:
        return 'bg-slate-500/20 text-slate-400';
    }
  }
</script>

<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">Containers</h1>
    <button
      on:click={openModal}
      class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700
             rounded-lg transition-colors"
    >
      <Plus class="w-4 h-4" />
      Create Container
    </button>
  </div>

  {#if loading}
    <div class="text-center text-slate-500 py-8">Loading...</div>
  {:else if error}
    <div class="bg-red-500/20 border border-red-500 text-red-400 px-4 py-3 rounded">
      {error}
    </div>
  {:else if containers.length === 0}
    <div class="bg-slate-800 rounded-lg p-8 text-center">
      <ContainerIcon class="w-16 h-16 text-slate-600 mx-auto mb-4" />
      <p class="text-slate-500">No containers found</p>
    </div>
  {:else}
    <div class="grid gap-4">
      {#each containers as container}
        <div class="bg-slate-800 rounded-lg p-4">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-4">
              <ContainerIcon class="w-8 h-8 text-blue-500" />
              <div>
                <h3 class="font-semibold">{container.name}</h3>
                <p class="text-sm text-slate-400">{container.image}</p>
              </div>
            </div>

            <div class="flex items-center gap-4">
              <span class="px-2 py-1 text-xs rounded {getStateColor(container.state)}">
                {container.state}
              </span>

              <div class="flex gap-1">
                {#if container.state === 'running'}
                  <button
                    on:click={() => handleStop(container.id)}
                    class="p-2 text-slate-400 hover:text-red-400 transition-colors"
                    title="Stop"
                  >
                    <Square class="w-4 h-4" />
                  </button>
                  <button
                    on:click={() => handleRestart(container.id)}
                    class="p-2 text-slate-400 hover:text-yellow-400 transition-colors"
                    title="Restart"
                  >
                    <RotateCw class="w-4 h-4" />
                  </button>
                {:else}
                  <button
                    on:click={() => handleStart(container.id)}
                    class="p-2 text-slate-400 hover:text-green-400 transition-colors"
                    title="Start"
                  >
                    <Play class="w-4 h-4" />
                  </button>
                {/if}
                <button
                  on:click={() => handleRemove(container.id, container.name)}
                  class="p-2 text-slate-400 hover:text-red-400 transition-colors"
                  title="Remove"
                >
                  <Trash2 class="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>

          {#if container.ports.length > 0}
            <div class="mt-3 text-sm text-slate-400">
              <span>Ports:</span>
              {#each container.ports as port}
                <span class="ml-2 font-mono">
                  {port.host_port || '?'}:{port.container_port}/{port.protocol}
                </span>
              {/each}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<!-- Create Container Modal -->
{#if showModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-xl font-semibold">Create Container</h2>
        <button on:click={closeModal} class="text-slate-400 hover:text-white">
          <X class="w-5 h-5" />
        </button>
      </div>

      <form on:submit|preventDefault={handleSubmit} class="space-y-4">
        <div>
          <label for="name" class="block text-sm text-slate-400 mb-1">Container Name</label>
          <input
            type="text"
            id="name"
            bind:value={formData.name}
            class="w-full px-3 py-2 bg-slate-700 rounded border border-slate-600
                   focus:border-blue-500 focus:outline-none"
            placeholder="my-container"
          />
        </div>

        <div>
          <label for="image" class="block text-sm text-slate-400 mb-1">Image</label>
          <input
            type="text"
            id="image"
            bind:value={formData.image}
            class="w-full px-3 py-2 bg-slate-700 rounded border border-slate-600
                   focus:border-blue-500 focus:outline-none"
            placeholder="nginx:latest"
          />
        </div>

        <div>
          <label for="restart" class="block text-sm text-slate-400 mb-1">Restart Policy</label>
          <select
            id="restart"
            bind:value={formData.restart_policy}
            class="w-full px-3 py-2 bg-slate-700 rounded border border-slate-600
                   focus:border-blue-500 focus:outline-none"
          >
            <option value="no">No</option>
            <option value="always">Always</option>
            <option value="unless-stopped">Unless Stopped</option>
            <option value="on-failure">On Failure</option>
          </select>
        </div>

        <!-- Port Mappings -->
        <div>
          <label class="block text-sm text-slate-400 mb-1">Port Mappings</label>
          <div class="flex gap-2 mb-2">
            <input
              type="text"
              bind:value={portMapping.host}
              placeholder="Host"
              class="w-24 px-3 py-2 bg-slate-700 rounded border border-slate-600
                     focus:border-blue-500 focus:outline-none"
            />
            <span class="text-slate-400 self-center">:</span>
            <input
              type="text"
              bind:value={portMapping.container}
              placeholder="Container"
              class="w-24 px-3 py-2 bg-slate-700 rounded border border-slate-600
                     focus:border-blue-500 focus:outline-none"
            />
            <select
              bind:value={portMapping.protocol}
              class="px-3 py-2 bg-slate-700 rounded border border-slate-600
                     focus:border-blue-500 focus:outline-none"
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
            </select>
            <button
              type="button"
              on:click={addPort}
              class="px-3 py-2 bg-slate-600 hover:bg-slate-500 rounded"
            >
              Add
            </button>
          </div>
          {#if portsList.length > 0}
            <div class="space-y-1">
              {#each portsList as port, i}
                <div class="flex items-center gap-2 text-sm bg-slate-700 px-2 py-1 rounded">
                  <span>{port.host}:{port.container}/{port.protocol}</span>
                  <button
                    type="button"
                    on:click={() => removePort(i)}
                    class="text-red-400 hover:text-red-300 ml-auto"
                  >
                    <X class="w-4 h-4" />
                  </button>
                </div>
              {/each}
            </div>
          {/if}
        </div>

        <!-- Environment Variables -->
        <div>
          <label class="block text-sm text-slate-400 mb-1">Environment Variables</label>
          <div class="flex gap-2 mb-2">
            <input
              type="text"
              bind:value={envVar.key}
              placeholder="KEY"
              class="flex-1 px-3 py-2 bg-slate-700 rounded border border-slate-600
                     focus:border-blue-500 focus:outline-none"
            />
            <span class="text-slate-400 self-center">=</span>
            <input
              type="text"
              bind:value={envVar.value}
              placeholder="value"
              class="flex-1 px-3 py-2 bg-slate-700 rounded border border-slate-600
                     focus:border-blue-500 focus:outline-none"
            />
            <button
              type="button"
              on:click={addEnvVar}
              class="px-3 py-2 bg-slate-600 hover:bg-slate-500 rounded"
            >
              Add
            </button>
          </div>
          {#if envList.length > 0}
            <div class="space-y-1">
              {#each envList as env, i}
                <div class="flex items-center gap-2 text-sm bg-slate-700 px-2 py-1 rounded">
                  <span class="font-mono">{env.key}={env.value}</span>
                  <button
                    type="button"
                    on:click={() => removeEnvVar(i)}
                    class="text-red-400 hover:text-red-300 ml-auto"
                  >
                    <X class="w-4 h-4" />
                  </button>
                </div>
              {/each}
            </div>
          {/if}
        </div>

        {#if error}
          <div class="text-red-400 text-sm">{error}</div>
        {/if}

        <div class="flex justify-end gap-3 pt-4">
          <button
            type="button"
            on:click={closeModal}
            class="px-4 py-2 bg-slate-600 hover:bg-slate-500 rounded-lg"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50"
          >
            {submitting ? 'Creating...' : 'Create Container'}
          </button>
        </div>
      </form>
    </div>
  </div>
{/if}
