import { messageOf } from "$lib/errors";
import { get } from "svelte/store";
import { getCertificateStatus, getCertificates, type Certificate, type CertificateStatus } from "$lib/api/security";
import { t } from "$lib/i18n";

/**
 * The web server's certificates (ADR-0404): what browsers get, the stored certificates, and the
 * serving or generation in progress, which the card of System > Security and its dialogs share.
 * The page creates it and loads it for an administrator.
 */
export class Certificates {
  status = $state<CertificateStatus | null>(null);
  list = $state<Certificate[]>([]);
  // Until the stored certificates have loaded, the card does not say that there are none.
  loaded = $state(false);
  loading = $state(false);
  // Why the last load of the certificates failed: reset by every load.
  loadError = $state("");
  // Why a delete or a serving failed; why a generation failed, shown in its dialog. What an action
  // did is a toast (ADR-0432).
  error = $state("");
  generateError = $state("");
  generateOpen = $state(false);

  async load() {
    this.loading = true;
    this.loadError = "";
    try {
      this.status = await getCertificateStatus();
      const resp = await getCertificates();
      this.list = resp.certificates;
      // The later answer of the two.
      this.status = resp.status;
      this.loaded = true;
    } catch (e) {
      // No list is known: not the one of an earlier load, and not "none".
      this.list = [];
      this.loaded = false;
      this.loadError = get(t)("account.certs.loadFailed", { error: messageOf(e) });
    } finally {
      this.loading = false;
    }
  }

  openGenerate() {
    this.generateError = "";
    this.generateOpen = true;
  }
}
