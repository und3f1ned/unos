<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { login, getAuthStatus } from '$lib/api/auth';
  import { user, isLoggedIn } from '$lib/stores/auth';
  import { connectWebSocket } from '$lib/stores/stats';
  import { Server } from 'lucide-svelte';

  let username = '';
  let password = '';
  let error = '';
  let loading = false;

  onMount(async () => {
    try {
      const status = await getAuthStatus();
      if (status.setup_required) {
        goto('/setup');
      }
    } catch {
      // API not available
    }
  });

  async function handleSubmit() {
    error = '';
    loading = true;

    try {
      await login(username, password);
      isLoggedIn.set(true);
      connectWebSocket();
      goto('/');
    } catch (e: any) {
      error = e.message || 'Login failed';
    } finally {
      loading = false;
    }
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-slate-900">
  <div class="bg-slate-800 p-8 rounded-lg shadow-xl w-full max-w-md">
    <div class="flex items-center justify-center gap-2 mb-8">
      <Server class="w-10 h-10 text-blue-500" />
      <span class="text-2xl font-bold">UNOS</span>
    </div>

    <form on:submit|preventDefault={handleSubmit} class="space-y-4">
      {#if error}
        <div class="bg-red-500/20 border border-red-500 text-red-400 px-4 py-2 rounded">
          {error}
        </div>
      {/if}

      <div>
        <label for="username" class="block text-sm font-medium text-slate-400 mb-1">
          Username
        </label>
        <input
          type="text"
          id="username"
          bind:value={username}
          class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg
                 focus:outline-none focus:border-blue-500"
          required
        />
      </div>

      <div>
        <label for="password" class="block text-sm font-medium text-slate-400 mb-1">
          Password
        </label>
        <input
          type="password"
          id="password"
          bind:value={password}
          class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg
                 focus:outline-none focus:border-blue-500"
          required
        />
      </div>

      <button
        type="submit"
        disabled={loading}
        class="w-full py-2 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800
               rounded-lg font-medium transition-colors"
      >
        {loading ? 'Signing in...' : 'Sign In'}
      </button>
    </form>
  </div>
</div>
