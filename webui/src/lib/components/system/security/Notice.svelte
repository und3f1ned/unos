<script lang="ts">
  import { AlertCircle, CheckCircle } from "lucide-svelte";

  // The outcome of a card's action above its content: a success or a failure, with its icon.
  type Props = {
    variant: "success" | "error";
    message: string;
  };

  let { variant, message }: Props = $props();

  const CLASSES = {
    success: "mb-4 p-3 bg-status-success/20 border border-status-success text-status-success rounded-lg flex items-center gap-2",
    error: "mb-4 p-3 bg-status-error/20 border border-status-error text-status-error rounded-lg flex items-center gap-2",
  };
</script>

<div class={CLASSES[variant]}>
  {#if variant === "success"}
    <CheckCircle class="w-5 h-5" />
  {:else}
    <AlertCircle class="w-5 h-5" />
  {/if}
  {message}
</div>
