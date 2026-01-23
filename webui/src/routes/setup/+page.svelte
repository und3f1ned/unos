<script lang="ts">
  import { goto } from '$app/navigation';
  import { setup, login } from '$lib/api/auth';
  import { connectWebSocket } from '$lib/stores/stats';
  import { Server } from 'lucide-svelte';

  let username = '';
  let password = '';
  let confirmPassword = '';
  let error = '';
  let loading = false;

  async function handleSubmit() {
    if (password !== confirmPassword) {
      error = 'Passwords do not match';
      return;
    }

    error = '';
    loading = true;

    try {
      await setup(username, password);
      await login(username, password);
      connectWebSocket();
      goto('/');
    } catch (e: any) {
      error = e.message || 'Setup failed';
    } finally {
      loading = false;
    }
  }
</script>

<div class="min-h-screen flex items-center justify-center bg-slate-900">
  <div class="bg-slate-800 p-8 rounded-lg shadow-xl w-full max-w-md">
    <div class="flex items-center justify-center gap-2 mb-4">
      <Server class="w-10 h-10 text-blue-500" />
      <span class="text-2xl font-bold">UNOS</span>
    </div>

    <p class="text-center text-slate-400 mb-8">
      Create your admin account to get started
    </p>

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
          minlength="8"
        />
      </div>

      <div>
        <label for="confirmPassword" class="block text-sm font-medium text-slate-400 mb-1">
          Confirm Password
        </label>
        <input
          type="password"
          id="confirmPassword"
          bind:value={confirmPassword}
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
        {loading ? 'Creating account...' : 'Create Admin Account'}
      </button>
    </form>
  </div>
</div>
