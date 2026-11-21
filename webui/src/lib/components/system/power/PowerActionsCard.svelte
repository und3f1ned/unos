<script lang="ts">
  import { Moon, Power, RotateCcw } from "lucide-svelte";
  import { Button, Card } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { Action } from "./actions";

  // Reboot, Shut down and Suspend: routine actions, so secondary buttons; the colour of danger is
  // the confirming button's in the dialog. The reboot or shutdown that waits for its
  // minute is the Status card's.
  type Props = {
    // The host's power state could not be read: the actions wait for it, and say why (disabledReason).
    unavailable: boolean;
    // An action was asked: it waits for its confirmation.
    onask: (action: Action) => void;
  };

  let { unavailable, onask }: Props = $props();
</script>

<Card title={$t("power.actions.title")} description={$t("power.actions.hint")}>
  <div class="flex flex-wrap items-center gap-2">
    <Button variant="secondary" icon={RotateCcw} disabled={unavailable}
      disabledReason={unavailable ? $t("power.actions.unavailable") : ""} onclick={() => onask("reboot")}>
      {$t("power.reboot.button")}
    </Button>
    <Button variant="secondary" icon={Power} disabled={unavailable}
      disabledReason={unavailable ? $t("power.actions.unavailable") : ""} onclick={() => onask("shutdown")}>
      {$t("power.shutdown.button")}
    </Button>
    <Button variant="secondary" icon={Moon} disabled={unavailable}
      disabledReason={unavailable ? $t("power.actions.unavailable") : ""} onclick={() => onask("suspend")}>
      {$t("power.suspend.button")}
    </Button>
  </div>
</Card>
