<script lang="ts">
  import { FileKey } from "lucide-svelte";
  import { generateSelfSignedCert } from "$lib/api/security";
  import { Alert, Button } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { Certificates } from "./certificates.svelte";

  // The form of a new self-signed certificate. What was typed stays for the next opening until a
  // certificate is generated.
  type Props = {
    certificates: Certificates;
  };

  let { certificates }: Props = $props();

  let newCert = $state({
    domain: "",
    validity_days: 365,
    organization: "",
    country: ""
  });

  async function handleGenerateCert() {
    if (!newCert.domain) {
      certificates.generateError = $t("account.certs.domainRequired");
      return;
    }

    certificates.loading = true;
    certificates.generateError = "";
    certificates.success = "";
    try {
      await generateSelfSignedCert({
        domain: newCert.domain,
        validity_days: newCert.validity_days,
        organization: newCert.organization || undefined,
        country: newCert.country || undefined
      });
      certificates.success = $t("account.certs.generated");
      certificates.generateOpen = false;
      newCert = { domain: "", validity_days: 365, organization: "", country: "" };
      await certificates.load();
    } catch (e: any) {
      certificates.generateError = e.message || $t("account.certs.generateFailed");
    } finally {
      certificates.loading = false;
    }
  }
</script>

<!-- Generate Self-Signed Certificate Modal -->
{#if certificates.generateOpen}
  <div class="modal-overlay items-start overflow-y-auto p-4">
    <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto">
      <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
        <FileKey class="w-5 h-5" />
        {$t("account.certs.generateTitle")}
      </h3>

      <form onsubmit={(e) => { e.preventDefault(); handleGenerateCert(); }} class="space-y-4">
        <!-- In the dialog: behind its overlay a refusal could not be read. -->
        {#if certificates.generateError}
          <Alert variant="error">{certificates.generateError}</Alert>
        {/if}

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.domain")}</label>
          <input
            type="text"
            bind:value={newCert.domain}
            class="form-input"
            placeholder={$t("account.certs.domainPlaceholder")}
            required
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.validity")}</label>
          <input
            type="number"
            bind:value={newCert.validity_days}
            min="1"
            max="3650"
            class="form-input"
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.organization")}</label>
          <input
            type="text"
            bind:value={newCert.organization}
            maxlength="64"
            class="form-input"
            placeholder={$t("account.certs.organizationPlaceholder")}
          />
        </div>

        <div>
          <label class="block text-sm font-medium text-content-secondary mb-1">{$t("account.certs.country")}</label>
          <input
            type="text"
            bind:value={newCert.country}
            minlength="2"
            maxlength="2"
            pattern="[A-Za-z][A-Za-z]"
            title={$t("account.certs.countryHint")}
            class="form-input"
            placeholder={$t("account.certs.countryPlaceholder")}
          />
        </div>

        <div class="flex gap-3">
          <Button variant="secondary" class="flex-1" type="button" onclick={() => (certificates.generateOpen = false)}>
            {$t("common.cancel")}
          </Button>
          <Button variant="primary" class="flex-1" type="submit" disabled={certificates.loading}>
            {certificates.loading ? $t("account.certs.generating") : $t("account.certs.generate")}
          </Button>
        </div>
      </form>
    </div>
  </div>
{/if}
