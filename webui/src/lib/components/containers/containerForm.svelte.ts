import type { ContainerCreate } from '$lib/api/containers';

type PortRow = { host: string; container: string; protocol: string };
type EnvRow = { key: string; value: string };

function emptyData(): ContainerCreate {
	return {
		name: '',
		image: '',
		ports: [],
		environment: {},
		restart_policy: 'unless-stopped'
	};
}

/**
 * The fields of the Create Container dialog: the page makes one, empties it each time it opens
 * the dialog, and sends what config() makes of it.
 */
export class ContainerForm {
	formData = $state<ContainerCreate>(emptyData());
	// The port and the variable being typed, and those added.
	portMapping = $state<PortRow>({ host: '', container: '', protocol: 'tcp' });
	envVar = $state<EnvRow>({ key: '', value: '' });
	portsList = $state<PortRow[]>([]);
	envList = $state<EnvRow[]>([]);

	reset() {
		this.formData = emptyData();
		this.portsList = [];
		this.envList = [];
		this.portMapping = { host: '', container: '', protocol: 'tcp' };
		this.envVar = { key: '', value: '' };
	}

	addPort() {
		if (this.portMapping.host && this.portMapping.container) {
			this.portsList = [...this.portsList, { ...this.portMapping }];
			this.portMapping = { host: '', container: '', protocol: 'tcp' };
		}
	}

	removePort(index: number) {
		this.portsList = this.portsList.filter((_, i) => i !== index);
	}

	addEnvVar() {
		if (this.envVar.key) {
			this.envList = [...this.envList, { ...this.envVar }];
			this.envVar = { key: '', value: '' };
		}
	}

	removeEnvVar(index: number) {
		this.envList = this.envList.filter((_, i) => i !== index);
	}

	/** What the server is asked to create. */
	config(): ContainerCreate {
		return {
			name: this.formData.name,
			image: this.formData.image,
			restart_policy: this.formData.restart_policy,
			ports: this.portsList.map((p) => ({
				host_port: parseInt(p.host),
				container_port: parseInt(p.container),
				protocol: p.protocol
			})),
			environment: this.envList.reduce((acc, e) => ({ ...acc, [e.key]: e.value }), {})
		};
	}
}
