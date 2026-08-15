# Build the image in Azure.
#
# `az acr build` rather than a local `docker build` so the toolkit works on a
# machine with no Docker daemon, and so the image is built on amd64 regardless
# of what the operator's laptop is. CI uses buildx instead; both paths are
# supported and documented, which is more than the predecessors managed — they
# had two paths that silently produced different images.

_ek_step_image() {
  local image="${EK_ACR}.azurecr.io/${EK_IMAGE}:latest"
  if [[ ! -f Dockerfile ]]; then
    ek_warn "No Dockerfile here; skipping the build. Deploy an image yourself, or run from the app repository."
    ek_step_record image skipped "" "no Dockerfile"
    return 0
  fi
  ek_az acr build --registry "$EK_ACR" --image "${EK_IMAGE}:latest" \
    --file Dockerfile . -o none
  ek_state_set names.image "$image"
  ek_step_record image done "$image"
  ek_step_done "$image"
}
ek_step_register image "Build and push the container image" _ek_step_image
