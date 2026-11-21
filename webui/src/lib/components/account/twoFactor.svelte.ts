import { messageOf } from "$lib/errors";
import { getTOTPStatus, type TOTPSetupResponse } from "$lib/api/auth";

/**
 * The account's two-factor authentication: its status, and the setup or the turning off in
 * progress, which the card of the Account page's Sign-in and security and its dialogs share. The page creates it and
 * loads the status.
 */
export class TwoFactor {
  // Until the status has loaded, the card offers neither Enable nor Disable: a status that could
  // not be loaded is reported, not shown as off.
  enabled = $state(false);
  // Every account must have two-factor authentication: turning it off says what follows.
  required = $state(false);
  statusLoaded = $state(false);
  statusLoading = $state(false);
  statusError = $state("");
  // A setup, an enable or a disable is waiting for the server.
  loading = $state(false);
  setupData = $state<TOTPSetupResponse | null>(null);
  // The code typed to confirm the setup.
  code = $state("");
  error = $state("");
  setupOpen = $state(false);
  disableOpen = $state(false);
  // The code typed to turn it off.
  disableCode = $state("");
  // The otpauth:// URI contains the TOTP secret, so the QR code is rendered in
  // the browser and never sent to a third-party service.
  qrDataUrl = $state("");

  async loadStatus() {
    this.statusLoading = true;
    try {
      const status = await getTOTPStatus();
      this.enabled = status.enabled;
      this.required = status.required;
      this.statusLoaded = true;
      this.statusError = "";
    } catch (e) {
      this.statusError = messageOf(e);
    } finally {
      this.statusLoading = false;
    }
  }
}
