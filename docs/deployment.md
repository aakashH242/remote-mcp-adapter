# Deployment

Which deployment paths are available, what each one needs, and the shortest path to getting the adapter running without building from source.

---

## Before you pick a deployment method

Remote MCP Adapter is easy to start, but there is one rule that matters no matter how you deploy it:

> The adapter and any upstream server that reads uploaded files or writes artifacts must agree on the same shared storage path.

If that shared path is wrong, file uploads and generated artifacts will fail in ways that look confusing and random.

In practice, that means:

- Docker Compose deployments should mount the same host directory into both containers.
- Helm/Kubernetes deployments should mount the same shared volume into the adapter and the upstream sidecars or services that need it.
- your runtime mount path should match the configured `storage.root`

There is one more rule that becomes important as soon as clients use helper-generated URLs:

> If users will call `<server_id>_get_upload_url(...)` or open HTTP artifact download links through a hostname, reverse proxy, ingress, or load balancer, set `core.public_base_url` to that exact external address.

On plain localhost the adapter can often guess a usable URL. In real deployments, guessing is fragile. The wrong value usually shows up as upload or download links that point at a pod address, container hostname, or some other internal URL the client cannot actually reach.

If you keep that one rule in mind, the rest of deployment becomes much more predictable.

---

## The two deployment options we document

There are two deployment paths we recommend and maintain in the docs:

- [Deploy with Docker Compose](deployment/compose.md)
- [Deploy with Helm](deployment/helm.md)

Choose the comparison view you want:

=== "Docker Compose"

	Best when you want:

	- the fastest path to a working end-to-end setup
	- a local or small-server deployment
	- a simple stack with one adapter and one upstream
	- something easy to inspect, stop, restart, and debug

	For Docker Compose users, note one detail: the repository's `compose.yaml` currently builds the adapter container from the local Dockerfile. That is convenient for contributors and local testing. If you want to avoid a local build, swap that service to use the published image instead.

=== "Helm"

	Best when you want:

	- Kubernetes deployment
	- repeatable cluster installs and upgrades
	- ingress, persistence, and pod-level configuration
	- either standalone sidecar upstreams or a more distributed setup

	The published artifacts are:

	- Docker image: `ghcr.io/aakashh242/remote-mcp-adapter`
	- Helm repository: `https://aakashh242.github.io/remote-mcp-adapter`

	For Helm users, the repository link above is the important one to share. It is the GitHub Pages root where Helm can reach `index.yaml`, not the `charts/` folder path from the source repository.

---

## Which one should you choose?

If you are unsure, use this rule of thumb:

- choose **Docker Compose** if you are evaluating the project, running it locally, or deploying it on one machine
- choose **Helm** if you already know you want Kubernetes

Compose is the easier starting point.
Helm is the better long-term fit once you care about cluster deployment, ingress, persistent volumes, and replica management.

If you already have your own container runtime or orchestrator and do not want either Compose or Helm, there is also a third practical path:

- run the published image directly
- mount your `config.yaml`
- mount shared storage at the path used by `storage.root`
- set `core.public_base_url` if clients will use helper-generated upload or download URLs

That path is valid. It is just less opinionated, so we do not document it as a full guided workflow here.

---

## Install options

Pick the deployment path you want to follow:

=== "Docker Compose"

	This is the easiest way to get the adapter running without building your own deployment layout.

	The repository ships a [compose.yaml](https://github.com/aakashH242/remote-mcp-adapter/blob/main/compose.yaml) that starts:

	- the adapter on port `8932`
	- a Playwright MCP upstream on port `8931`
	- a shared mounted directory at `./data`
	- a mounted adapter config at `/etc/remote-mcp-adapter/config.yaml`

	**What you need**

	Before you use the Compose path, make sure you have:

	- Docker installed
	- Docker Compose available
	- a copy of the repository
	- a `config.yaml` that matches your intended upstream setup

	**Quick start command**

	```bash
	git clone https://github.com/aakashH242/remote-mcp-adapter.git
	cd remote-mcp-adapter
	docker compose up -d
	```

	**When Compose is the right choice**

	Choose Compose when:

	- you want the shortest path to a working setup
	- you are testing or demoing the adapter
	- you do not need Kubernetes yet
	- one machine is enough

=== "Helm"

	Use Helm when you want to run the adapter in Kubernetes using the published chart.

	The chart supports both of these deployment styles:

	- **standalone** - the adapter runs together with configured upstream sidecars in the same pod
	- **distributed** - the adapter runs in its own deployment and upstream services are expected to exist separately

	For end users, the normal path is the **published Helm repository**:

	- `https://aakashh242.github.io/remote-mcp-adapter`

	The local chart under [charts/remote-mcp-adapter](https://github.com/aakashH242/remote-mcp-adapter/tree/main/charts/remote-mcp-adapter) is mainly useful when:

	- you are testing unreleased changes
	- you are contributing to the chart itself
	- you want to inspect or modify values locally before publishing anything

	**What you need**

	Before you use the Helm path, make sure you have:

	- a Kubernetes cluster
	- Helm 3
	- a values file or overrides that match your environment
	- shared storage planned correctly for your chosen deployment mode
	- an ingress or load balancer plan if the service will be reachable externally

	For the normal published-repository flow, you do **not** need a local checkout of this repository.

	Also note the chart's Kubernetes support window in [charts/remote-mcp-adapter/Chart.yaml](https://github.com/aakashH242/remote-mcp-adapter/blob/main/charts/remote-mcp-adapter/Chart.yaml):

	- Kubernetes `>= 1.29.0`
	- Kubernetes `< 1.36.0`

	**Quick start command**

	For most users, this is the right install path:

	```bash
	helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
	helm repo update
	helm search repo remote-mcp-adapter
	helm install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter
	```

	If you are working from a local checkout and want to install the chart directly from the repository instead of the published repo:

	```bash
	helm install remote-mcp-adapter ./charts/remote-mcp-adapter -f ./charts/remote-mcp-adapter/values.yaml
	```

	**When Helm is the right choice**

	Choose Helm when:

	- you already deploy services on Kubernetes
	- you want ingress, persistence, and pod-level controls
	- you need a repeatable cluster deployment story
	- you want to use the published Helm repository rather than wiring manifests by hand

---

## A quick note on the published image and chart

You do **not** need to build the adapter from source just to deploy it.

For normal deployments, the intended artifacts are already published:

- the adapter container image is published to GHCR
- the Helm chart is published through the repository's chart release workflow

That means:

- Compose deployments can use the published image instead of requiring a local image build, even though the repository's default `compose.yaml` currently builds locally
- Helm deployments can install from the published Helm repository instead of requiring a local checkout

Using published artifacts is usually the right choice for end users. Building from source is more useful for contributors and local development.

---

## How deployment relates to the rest of the docs

Think of the docs flow like this:

- [Getting Started](getting-started.md) is for getting something running quickly, especially locally
- this page is for choosing a deployment path
- [Configuration](configuration.md) is for shaping the runtime behavior once you know how you want to run it
- [Security](security/index.md) and [Telemetry](telemetry.md) are for hardening and operating it well

---

## Next steps

- **Previous topic:** [Telemetry](telemetry.md) — metrics, queues, and runtime visibility.
- **Next:** [Deploy with Docker Compose](deployment/compose.md) — the simplest end-to-end deployment path.
- **See also:** [Getting Started](getting-started.md) — fastest local path if you just want to try it.
- **See also:** [Configuration](configuration.md) — choose a scenario and tune the adapter for your environment.
