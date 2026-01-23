<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';

  onMount(() => {
    goto('/system#general');
  });
</script>

<div class="flex items-center justify-center h-64">
  <p class="text-slate-500">Redirecting to System settings...</p>
</div>
