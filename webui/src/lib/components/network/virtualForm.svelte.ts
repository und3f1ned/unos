// The fields of the dialog that makes a bond, a VLAN or a bridge (ADR-0424). The card opens it for
// a kind, with the defaults of that kind, and checks and sends what it holds.
import type { Addressing, VirtualKind } from "$lib/api/network";

export class VirtualForm {
  // The kind being made; null while the dialog is closed.
  creating = $state<VirtualKind | null>(null);
  // Why the server or the card refused it.
  error = $state("");
  name = $state("");
  mode = $state<"802.3ad" | "balance-rr">("802.3ad");
  ports = $state<string[]>([]);
  parent = $state("");
  vlanId = $state("");
  mtu = $state("");
  addressing = $state<Addressing>("none");
  addressesText = $state("");

  open(kind: VirtualKind) {
    this.creating = kind;
    this.error = "";
    this.name = kind === "bond" ? "bond0" : kind === "bridge" ? "br0" : "";
    this.mode = "802.3ad";
    this.ports = [];
    this.parent = "";
    this.vlanId = "";
    this.mtu = "";
    this.addressing = "none";
    this.addressesText = "";
  }

  togglePort(port: string, checked: boolean) {
    this.ports = checked ? [...this.ports.filter((p) => p !== port), port] : this.ports.filter((p) => p !== port);
  }

  /** The MTU: null when left empty, undefined when it is no whole number from 1280 to 9216. */
  parsedMtu(): number | null | undefined {
    if (!this.mtu.trim()) return null;
    const value = Number(this.mtu);
    return Number.isInteger(value) && value >= 1280 && value <= 9216 ? value : undefined;
  }

  /** The static addresses, separated by commas. */
  addresses(): string[] {
    return this.addressesText
      .split(",")
      .map((a) => a.trim())
      .filter((a) => a);
  }
}
