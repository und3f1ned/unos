// Stand-in for the web UI container's Caddy (webui/Caddyfile) on this machine,
// for the local E2E runs of scripts/standin/e2e.sh. Node.js standard library only.
//
//   HTTPS: the static build with the SPA fallback (try_files {path} /index.html),
//          /api/* and /ws/* (WebSocket upgrades included) proxied to the
//          supervisor, and the Caddyfile's security headers on every answer,
//          proxied ones included ("defer").
//   HTTP:  /api/v1/health proxied; everything else "redir https://{host}{uri} 308"
//          ({host} without the port, as in Caddy).
//
// Both listen on 127.0.0.1 and, where the machine has it, ::1.
//
// HTTPS serves <servedFile> while it holds a certificate and its key that load,
// as Caddy serves the served certificate that the supervisor writes (ADR-0404),
// from the next connection after each change on; else <certFile> and <keyFile>,
// as Caddy serves its local CA's.
//
// Usage: node front.mjs <buildDir> <httpsPort> <httpPort> <backendPort> <certFile> <keyFile> <servedFile>
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import path from 'node:path';
import tls from 'node:tls';

const [, , buildDirArg, httpsPort, httpPort, backendPort, certFile, keyFile, servedFile] = process.argv;
if (!servedFile) {
	console.error(
		'usage: node front.mjs <buildDir> <httpsPort> <httpPort> <backendPort> <certFile> <keyFile> <servedFile>',
	);
	process.exit(2);
}
const buildDir = path.resolve(buildDirArg);

// The header block of the HTTPS site in webui/Caddyfile, value for value
// ({hostport} is filled in per request, as Caddy does).
// supervisor/tests/test_standin.py checks that the two are equal.
const SECURITY_HEADERS = {
	'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' wss://{hostport}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
	'X-Content-Type-Options': 'nosniff',
	'X-Frame-Options': 'DENY',
	'Referrer-Policy': 'no-referrer',
	'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
};

const TYPES = {
	'.html': 'text/html; charset=utf-8',
	'.js': 'text/javascript; charset=utf-8',
	'.css': 'text/css; charset=utf-8',
	'.json': 'application/json',
	'.png': 'image/png',
	'.svg': 'image/svg+xml',
	'.ico': 'image/x-icon',
	'.woff': 'font/woff',
	'.woff2': 'font/woff2',
	'.txt': 'text/plain; charset=utf-8',
	'.webmanifest': 'application/manifest+json',
};

function log(...parts) {
	console.log(new Date().toISOString().slice(11, 19), ...parts);
}

/** Lower-case names, so that they replace the upstream's headers of the same name. */
function securityHeaders(req) {
	const headers = {};
	for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
		headers[name.toLowerCase()] = value.replace('{hostport}', req.headers.host || '');
	}
	return headers;
}

/** The request's headers for the supervisor: X-Forwarded-* are set here, never passed on (Caddy's default). */
function forwardedHeaders(req, proto) {
	const headers = {};
	for (const [name, value] of Object.entries(req.headers)) {
		if (!name.startsWith('x-forwarded-')) headers[name] = value;
	}
	headers['x-forwarded-for'] = req.socket.remoteAddress;
	headers['x-forwarded-proto'] = proto;
	if (req.headers.host) headers['x-forwarded-host'] = req.headers.host;
	return headers;
}

function proxy(req, res, proto, withSecurityHeaders) {
	const upstream = http.request(
		{ host: '127.0.0.1', port: backendPort, method: req.method, path: req.url, headers: forwardedHeaders(req, proto) },
		(ures) => {
			res.writeHead(ures.statusCode, { ...ures.headers, ...(withSecurityHeaders ? securityHeaders(req) : {}) });
			ures.pipe(res);
		},
	);
	upstream.on('error', (err) => {
		log('proxy error', req.method, req.url.split('?')[0], err.code || err.message);
		if (!res.headersSent) res.writeHead(502, withSecurityHeaders ? securityHeaders(req) : {});
		res.end('bad gateway');
	});
	req.pipe(upstream);
}

function serveStatic(req, res) {
	let pathname;
	try {
		pathname = decodeURIComponent(new URL(req.url, 'https://front.invalid').pathname);
	} catch {
		res.writeHead(400, securityHeaders(req));
		return res.end('bad request');
	}
	let file = path.normalize(path.join(buildDir, pathname));
	const inside = file === buildDir || file.startsWith(buildDir + path.sep);
	if (inside && fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
	if (!inside || !fs.existsSync(file) || !fs.statSync(file).isFile()) file = path.join(buildDir, 'index.html');
	res.writeHead(200, {
		'content-type': TYPES[path.extname(file)] || 'application/octet-stream',
		...securityHeaders(req),
	});
	if (req.method === 'HEAD') return res.end();
	fs.createReadStream(file).pipe(res);
}

function httpsHandler(req, res) {
	if (req.url.startsWith('/api/') || req.url.startsWith('/ws/')) {
		if (req.url.startsWith('/api/')) log('API', req.method, req.url.split('?')[0]);
		return proxy(req, res, 'https', true);
	}
	return serveStatic(req, res);
}

function upgradeHandler(req, socket, head) {
	if (!req.url.startsWith('/ws/')) return socket.destroy();
	log('WS', req.url.split('?')[0]);
	const upstream = net.connect(Number(backendPort), '127.0.0.1', () => {
		let raw = `${req.method} ${req.url} HTTP/1.1\r\n`;
		for (let i = 0; i < req.rawHeaders.length; i += 2) {
			if (!req.rawHeaders[i].toLowerCase().startsWith('x-forwarded-')) {
				raw += `${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`;
			}
		}
		raw += `X-Forwarded-For: ${req.socket.remoteAddress}\r\nX-Forwarded-Proto: https\r\n\r\n`;
		upstream.write(raw);
		if (head?.length) upstream.write(head);
		socket.pipe(upstream).pipe(socket);
	});
	upstream.on('error', () => socket.destroy());
	socket.on('error', () => upstream.destroy());
}

function httpHandler(req, res) {
	if (req.url.split('?')[0] === '/api/v1/health') return proxy(req, res, 'http', false);
	const host = (req.headers.host || '').replace(/:\d+$/, '');
	res.writeHead(308, { location: `https://${host}${req.url}` });
	res.end();
}

function listen(server, port, address, required) {
	server.on('error', (err) => {
		if (required) {
			console.error(`front.mjs: cannot listen on [${address}]:${port}: ${err.message}`);
			process.exit(1);
		}
		log(`not listening on [${address}]:${port}: ${err.code || err.message}`);
	});
	server.listen(Number(port), address, () => log(`listening on [${address}]:${port}`));
}

const ownTls = { cert: fs.readFileSync(certFile), key: fs.readFileSync(keyFile) };
const secureServers = [];
// The served file as last read (its inode, size and time): it is read again only when it changed.
let servedStamp = '';

/** Before each TLS connection: the served file's certificate when it changed and loads, else our own. */
function followServedFile() {
	const stat = fs.statSync(servedFile, { throwIfNoEntry: false });
	const stamp = stat ? `${stat.ino}:${stat.size}:${stat.mtimeMs}` : '';
	if (stamp === servedStamp) return;
	servedStamp = stamp;
	let options = ownTls;
	if (stat) {
		try {
			// Read inside: the supervisor may have removed it since.
			const pem = fs.readFileSync(servedFile);
			tls.createSecureContext({ cert: pem, key: pem });
			options = { cert: pem, key: pem };
		} catch (err) {
			log('the served certificate does not load, our own instead:', err.message);
		}
	}
	log(options === ownTls ? 'serving our own certificate' : 'serving the served certificate');
	for (const server of secureServers) server.setSecureContext(options);
}

for (const [address, required] of [['127.0.0.1', true], ['::1', false]]) {
	const secure = https.createServer(ownTls, httpsHandler);
	// Before the TLS socket is made with the server's context, which it takes at once.
	secure.prependListener('connection', followServedFile);
	secure.on('upgrade', upgradeHandler);
	secureServers.push(secure);
	listen(secure, httpsPort, address, required);
	listen(http.createServer(httpHandler), httpPort, address, required);
}
