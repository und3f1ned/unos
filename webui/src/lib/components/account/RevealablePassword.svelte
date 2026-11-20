<script lang="ts">
  import { Eye, EyeOff } from "lucide-svelte";
  import { Button } from "$lib/components/ui";

  // A password field with a button that shows or hides what was typed.
  type Props = {
    id: string;
    value: string;
    minlength?: number;
  };

  let { id, value = $bindable(), minlength }: Props = $props();

  let shown = $state(false);
</script>

<div class="relative">
  {#if shown}
    <input
      type="text"
      {id}
      bind:value
      {minlength}
      class="form-input pr-10"
      required
    />
  {:else}
    <input
      type="password"
      {id}
      bind:value
      {minlength}
      class="form-input pr-10"
      required
    />
  {/if}
  <Button variant="plain" class="absolute right-3 top-1/2 -translate-y-1/2 text-content-secondary hover:text-content-soft"
    type="button"
    onclick={() => (shown = !shown)}
  >
    {#if shown}
      <EyeOff class="w-4 h-4" />
    {:else}
      <Eye class="w-4 h-4" />
    {/if}
  </Button>
</div>
