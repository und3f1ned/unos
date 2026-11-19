<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { serveCertificate, LOCAL_CA_ID } from "$lib/api/security";
  import { Alert, Button, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import type { Certificates } from "./certificates.svelte";

  // The confirmation of serving a stored certificate, or Caddy's local CA again.
  type Props = {
    certificates: Certificates;
  };

  let { certificates }: Props = $props();

  /** A certificate's date (UTC with its offset, ADR-0401) in the language of the page and the browser's time zone. */
  let formatCertificateDate = $derived((value: string): string => formatDateTime(value, $intlLocale));

  function closeServe() {
    if (!certificates.serveBusy) certificates.serveTarget = null;
  }

  async function confirmServe() {
    if (!certificates.serveTarget) return;
    const cert = certificates.serveTarget.cert;
    certificates.serveBusy = true;
    certificates.serveError = "";
    certificates.success = "";
    certificates.error = "";
    try {
      const answer = await serveCertificate(cert ? cert.id : LOCAL_CA_ID);
      certificates.status = answer.status;
      certificates.success = cert ? $t("account.certs.nowServed", { domain: cert.domain }) : $t("account.certs.nowLocalCa");
      certificates.serveBusy = false;
      certificates.serveTarget = null;
      await certificates.load();
    } catch (e) {
      certificates.serveError = messageOf(e);
    } finally {
      certificates.serveBusy = false;
    }
  }
</script>

<!-- Serve a stored certificate, or Caddy's local CA again: every browser sees a new certificate (ADR-0404). -->
<Modal
  open={certificates.serveTarget !== null}
  title={certificates.serveTarget?.cert ? $t("account.certs.serveTitle") : $t("account.certs.localCaTitle")}
  size="md"
  closable={!certificates.serveBusy}
  onclose={closeServe}
>
  {#if certificates.serveTarget}
    <p class="break-words">
      {certificates.serveTarget.cert
        ? $t("account.certs.serveText", { domain: certificates.serveTarget.cert.domain })
        : $t("account.certs.localCaText")}
    </p>
    <Alert variant="warning" class="mt-3">{$t("account.certs.newCertificateWarning")}</Alert>
    {#if certificates.serveTarget.cert?.expires_soon && certificates.serveTarget.cert.valid_until}
      <Alert variant="warning" class="mt-3">
        {$t("account.certs.expiresSoonWarning", { date: formatCertificateDate(certificates.serveTarget.cert.valid_until) })}
      </Alert>
    {/if}
    {#if certificates.serveError}
      <Alert variant="error" class="mt-3 break-words">{certificates.serveError}</Alert>
    {/if}
  {/if}
  {#snippet footer()}
    <Button variant="secondary" class="flex-1" disabled={certificates.serveBusy} onclick={closeServe}>
      {$t("common.cancel")}
    </Button>
    <Button variant="primary" class="flex-1" loading={certificates.serveBusy} onclick={confirmServe}>
      {certificates.serveTarget?.cert ? $t("account.certs.serveConfirm") : $t("account.certs.useLocalCa")}
    </Button>
  {/snippet}
</Modal>
