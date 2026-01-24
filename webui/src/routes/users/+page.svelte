<script lang="ts">
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import {
    getUsers,
    createUser,
    updateUser,
    deleteUser,
    type User
  } from "$lib/api/auth";
  import { user as currentUser } from "$lib/stores/auth";
  import {
    Users,
    UserPlus,
    Shield,
    ShieldOff,
    Trash2,
    RefreshCw,
    Edit,
    X
  } from "lucide-svelte";

  let users: User[] = [];
  let loading = true;
  let error = "";
  let success = "";

  // Modal states
  let showCreateModal = false;
  let showEditModal = false;
  let showDeleteModal = false;

  // Form data
  let newUsername = "";
  let newPassword = "";
  let confirmPassword = "";
  let editingUser: User | null = null;
  let editPassword = "";
  let editIsAdmin = false;
  let deletingUser: User | null = null;

  onMount(async () => {
    // Redirect non-admin users
    if (!$currentUser?.is_admin) {
      goto("/");
      return;
    }
    await loadUsers();
  });

  // Also watch for user changes (in case they navigate here before user loads)
  $: if ($currentUser !== null && !$currentUser.is_admin) {
    goto("/");
  }

  async function loadUsers() {
    loading = true;
    error = "";
    try {
      users = await getUsers();
    } catch (e) {
      error = e instanceof Error ? e.message : "Failed to load users";
    } finally {
      loading = false;
    }
  }

  function openCreateModal() {
    newUsername = "";
    newPassword = "";
    confirmPassword = "";
    showCreateModal = true;
  }

  async function handleCreateUser() {
    if (!newUsername || !newPassword) {
      error = "Please fill in all fields";
      return;
    }
    if (newPassword !== confirmPassword) {
      error = "Passwords do not match";
      return;
    }
    if (newPassword.length < 6) {
      error = "Password must be at least 6 characters";
      return;
    }

    error = "";
    try {
      await createUser({ username: newUsername, password: newPassword });
      success = "User \"" + newUsername + "\" created successfully";
      setTimeout(() => success = "", 3000);
      showCreateModal = false;
      await loadUsers();
    } catch (e) {
      error = e instanceof Error ? e.message : "Failed to create user";
    }
  }

  function openEditModal(user: User) {
    editingUser = user;
    editPassword = "";
    editIsAdmin = user.is_admin;
    showEditModal = true;
  }

  async function handleUpdateUser() {
    if (!editingUser) return;

    error = "";
    try {
      const data: { is_admin?: boolean; password?: string } = {};
      if (editIsAdmin !== editingUser.is_admin) {
        data.is_admin = editIsAdmin;
      }
      if (editPassword) {
        if (editPassword.length < 6) {
          error = "Password must be at least 6 characters";
          return;
        }
        data.password = editPassword;
      }

      if (Object.keys(data).length === 0) {
        showEditModal = false;
        return;
      }

      await updateUser(editingUser.id, data);
      success = "User \"" + editingUser.username + "\" updated successfully";
      setTimeout(() => success = "", 3000);
      showEditModal = false;
      await loadUsers();
    } catch (e) {
      error = e instanceof Error ? e.message : "Failed to update user";
    }
  }

  function openDeleteModal(user: User) {
    deletingUser = user;
    showDeleteModal = true;
  }

  async function handleDeleteUser() {
    if (!deletingUser) return;

    error = "";
    try {
      await deleteUser(deletingUser.id);
      success = "User \"" + deletingUser.username + "\" deleted successfully";
      setTimeout(() => success = "", 3000);
      showDeleteModal = false;
      await loadUsers();
    } catch (e) {
      error = e instanceof Error ? e.message : "Failed to delete user";
    }
  }

  function formatDate(date: string | null): string {
    if (!date) return "Unknown";
    return new Date(date).toLocaleString();
  }
</script>

<!-- Only render content if user is admin -->
{#if $currentUser?.is_admin}
<div class="space-y-6">
  <div class="flex items-center justify-between">
    <h1 class="text-2xl font-bold">User Management</h1>
    <button
      on:click={loadUsers}
      class="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
      disabled={loading}
    >
      <RefreshCw class="w-4 h-4 {loading ? 'animate-spin' : ''}" />
      Refresh
    </button>
  </div>

  {#if error}
    <div class="bg-red-500/20 border border-red-500 text-red-400 px-4 py-3 rounded-lg">
      {error}
    </div>
  {/if}

  {#if success}
    <div class="bg-green-500/20 border border-green-500 text-green-400 px-4 py-3 rounded-lg">
      {success}
    </div>
  {/if}

  {#if loading}
    <div class="flex items-center justify-center py-12">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
    </div>
  {:else}
    <!-- Users List -->
    <div class="bg-slate-800 rounded-lg p-6">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold flex items-center gap-2">
          <Users class="w-5 h-5 text-blue-500" />
          Users ({users.length})
        </h2>
        <button
          on:click={openCreateModal}
          class="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition-colors"
        >
          <UserPlus class="w-4 h-4" />
          Add User
        </button>
      </div>

      {#if users.length === 0}
        <p class="text-slate-400">No users found</p>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="text-left text-slate-400 border-b border-slate-700">
                <th class="pb-3">Username</th>
                <th class="pb-3">Role</th>
                <th class="pb-3">2FA</th>
                <th class="pb-3">Created</th>
                <th class="pb-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {#each users as user}
                <tr class="border-b border-slate-700">
                  <td class="py-3">
                    <div class="flex items-center gap-2">
                      <span class="font-medium">{user.username}</span>
                      {#if $currentUser?.id === user.id}
                        <span class="px-2 py-0.5 text-xs bg-blue-600 rounded">You</span>
                      {/if}
                    </div>
                  </td>
                  <td class="py-3">
                    {#if user.is_admin}
                      <span class="flex items-center gap-1 text-yellow-400">
                        <Shield class="w-4 h-4" />
                        Admin
                      </span>
                    {:else}
                      <span class="flex items-center gap-1 text-slate-400">
                        <ShieldOff class="w-4 h-4" />
                        User
                      </span>
                    {/if}
                  </td>
                  <td class="py-3">
                    {#if user.totp_enabled}
                      <span class="text-green-400 text-sm">Enabled</span>
                    {:else}
                      <span class="text-slate-500 text-sm">Disabled</span>
                    {/if}
                  </td>
                  <td class="py-3 text-slate-400">
                    {formatDate(user.created_at)}
                  </td>
                  <td class="py-3 text-right">
                    <div class="flex items-center justify-end gap-2">
                      <button
                        on:click={() => openEditModal(user)}
                        class="p-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
                        title="Edit user"
                      >
                        <Edit class="w-4 h-4" />
                      </button>
                      {#if $currentUser?.id !== user.id}
                        <button
                          on:click={() => openDeleteModal(user)}
                          class="p-2 bg-red-600/30 hover:bg-red-600 rounded-lg transition-colors"
                          title="Delete user"
                        >
                          <Trash2 class="w-4 h-4" />
                        </button>
                      {/if}
                    </div>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </div>

    <!-- Info Section -->
    <div class="bg-slate-800 rounded-lg p-6">
      <h2 class="text-lg font-semibold mb-4">About User Management</h2>
      <div class="space-y-2 text-slate-400 text-sm">
        <p>• Admin users have full access to all UNOS features including user management.</p>
        <p>• Regular users can access basic features but cannot manage other users.</p>
        <p>• You cannot delete your own account.</p>
        <p>• Passwords must be at least 6 characters long.</p>
      </div>
    </div>
  {/if}
</div>

<!-- Create User Modal -->
{#if showCreateModal}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full mx-4">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-xl font-semibold flex items-center gap-2">
          <UserPlus class="w-5 h-5" />
          Create New User
        </h3>
        <button on:click={() => showCreateModal = false} class="text-slate-400 hover:text-white">
          <X class="w-5 h-5" />
        </button>
      </div>

      <div class="space-y-4">
        <div>
          <label for="newUsername" class="block text-sm text-slate-400 mb-1">Username</label>
          <input
            type="text"
            id="newUsername"
            bind:value={newUsername}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="Enter username"
          />
        </div>
        <div>
          <label for="newPassword" class="block text-sm text-slate-400 mb-1">Password</label>
          <input
            type="password"
            id="newPassword"
            bind:value={newPassword}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="Enter password"
          />
        </div>
        <div>
          <label for="confirmPassword" class="block text-sm text-slate-400 mb-1">Confirm Password</label>
          <input
            type="password"
            id="confirmPassword"
            bind:value={confirmPassword}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="Confirm password"
          />
        </div>
      </div>

      <div class="flex justify-end gap-3 mt-6">
        <button
          on:click={() => showCreateModal = false}
          class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          on:click={handleCreateUser}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition-colors"
        >
          Create User
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- Edit User Modal -->
{#if showEditModal && editingUser}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full mx-4">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-xl font-semibold flex items-center gap-2">
          <Edit class="w-5 h-5" />
          Edit User: {editingUser.username}
        </h3>
        <button on:click={() => showEditModal = false} class="text-slate-400 hover:text-white">
          <X class="w-5 h-5" />
        </button>
      </div>

      <div class="space-y-4">
        <div>
          <label class="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              bind:checked={editIsAdmin}
              class="w-5 h-5 rounded bg-slate-700 border-slate-600"
            />
            <span>Administrator privileges</span>
          </label>
        </div>
        <div>
          <label for="editPassword" class="block text-sm text-slate-400 mb-1">New Password (leave blank to keep current)</label>
          <input
            type="password"
            id="editPassword"
            bind:value={editPassword}
            class="w-full px-4 py-2 bg-slate-700 border border-slate-600 rounded-lg focus:outline-none focus:border-blue-500"
            placeholder="Enter new password"
          />
        </div>
      </div>

      <div class="flex justify-end gap-3 mt-6">
        <button
          on:click={() => showEditModal = false}
          class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          on:click={handleUpdateUser}
          class="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition-colors"
        >
          Save Changes
        </button>
      </div>
    </div>
  </div>
{/if}

<!-- Delete User Modal -->
{#if showDeleteModal && deletingUser}
  <div class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-slate-800 rounded-lg p-6 max-w-md w-full mx-4">
      <h3 class="text-xl font-semibold mb-4">Delete User</h3>
      <p class="text-slate-400 mb-6">
        Are you sure you want to delete user <strong class="text-white">{deletingUser.username}</strong>? This action cannot be undone.
      </p>
      <div class="flex justify-end gap-3">
        <button
          on:click={() => showDeleteModal = false}
          class="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          on:click={handleDeleteUser}
          class="px-4 py-2 bg-red-600 hover:bg-red-500 rounded-lg transition-colors"
        >
          Delete User
        </button>
      </div>
    </div>
  </div>
{/if}
{/if}
