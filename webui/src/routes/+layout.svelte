<script lang="ts">
  import "../app.css";
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import { page } from "$app/stores";
  import { user, isLoggedIn } from "$lib/stores/auth";
  import { connectWebSocket, disconnectWebSocket } from "$lib/stores/stats";
  import { getMe, isAuthenticated, logout } from "$lib/api/auth";
  import {
    LayoutDashboard,
    HardDrive,
    Share2,
    Container,
    LogOut,
    Server,
    Package,
    FolderOpen,
    Users,
    Wrench,
    Archive
  } from "lucide-svelte";

  // Base navigation items (always visible)
  const baseNavItems = [
    { href: "/", icon: LayoutDashboard, label: "Dashboard" },
    { href: "/storage", icon: HardDrive, label: "Storage" },
    { href: "/snapshots", icon: Archive, label: "Snapshots" },
    { href: "/shares", icon: Share2, label: "Shares" },
    { href: "/files", icon: FolderOpen, label: "Files" },
    { href: "/containers", icon: Container, label: "Containers" },
    { href: "/apps", icon: Package, label: "App Store" },
    { href: "/system", icon: Wrench, label: "System" },
  ];

  // Admin-only navigation items
  const adminNavItems = [
    { href: "/users", icon: Users, label: "Users" },
  ];

  // Reactive nav items based on user role
  $: navItems = $user?.is_admin
    ? [...baseNavItems.slice(0, 7), ...adminNavItems, baseNavItems[7]]
    : baseNavItems;

  onMount(async () => {
    if (!isAuthenticated()) {
      if (!$page.url.pathname.startsWith("/login") && !$page.url.pathname.startsWith("/setup")) {
        goto("/login");
      }
      return;
    }

    try {
      const me = await getMe();
      user.set(me);
      isLoggedIn.set(true);
      connectWebSocket();
    } catch {
      logout();
      goto("/login");
    }
  });

  function handleLogout() {
    logout();
    disconnectWebSocket();
    user.set(null);
    isLoggedIn.set(false);
    goto("/login");
  }

  // Reactive pathname - this ensures menu updates on navigation
  $: pathname = $page.url.pathname;

  // Check if current path matches nav item
  function isActive(href: string, currentPath: string): boolean {
    if (href === "/") return currentPath === "/";
    return currentPath === href || currentPath.startsWith(href + "/");
  }
</script>

{#if $page.url.pathname.startsWith("/login") || $page.url.pathname.startsWith("/setup")}
  <slot />
{:else}
  <div class="flex h-screen">
    <aside class="w-56 bg-slate-800 flex flex-col">
      <div class="p-4 border-b border-slate-700">
        <div class="flex items-center gap-2">
          <Server class="w-7 h-7 text-blue-500" />
          <span class="text-lg font-bold">UNOS</span>
        </div>
      </div>

      <nav class="flex-1 p-3 overflow-y-auto">
        <ul class="space-y-1">
          {#each navItems as item}
            <li>
              <a
                href={item.href}
                class="flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-sm {isActive(item.href, pathname) ? 'bg-blue-600 text-white' : 'text-slate-300 hover:bg-slate-700'}"
              >
                <svelte:component this={item.icon} class="w-5 h-5" />
                {item.label}
              </a>
            </li>
          {/each}
        </ul>
      </nav>

      <div class="p-3 border-t border-slate-700">
        {#if $user}
          <div class="flex items-center justify-between">
            <span class="text-sm text-slate-400">{$user.username}</span>
            <button
              on:click={handleLogout}
              class="p-2 text-slate-400 hover:text-white transition-colors"
              title="Logout"
            >
              <LogOut class="w-4 h-4" />
            </button>
          </div>
        {/if}
      </div>
    </aside>

    <main class="flex-1 overflow-auto bg-slate-900 p-6">
      <slot />
    </main>
  </div>
{/if}
