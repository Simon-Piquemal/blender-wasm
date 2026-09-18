# Image de déploiement du Blender wasm — un serveur statique, rien de plus.
#
# ── POURQUOI ON NE CONSTRUIT PAS ICI ──────────────────────────────────────
#
# Construire Blender pour WebAssembly demande emsdk, une chaîne CMake complète
# et des heures de CPU (cf. `Makefile` et `.github/workflows/build-release.yml`).
# Ce n'est pas quelque chose qu'un builder Dokploy peut faire : il tomberait en
# manque de mémoire, et il le referait à chaque déploiement pour un binaire qui
# ne change presque jamais.
#
# L'artefact est donc construit une fois sur une machine de développement
# (`cd demo && pnpm build`) et VERSIONNÉ dans `demo/dist/`. Cette image ne fait
# que le servir. C'est le même arbitrage que pour n'importe quel binaire
# volumineux : on paie le poids dans git pour ne plus jamais payer le build.
#
# ⚠️ CONSÉQUENCE À CONNAÎTRE : modifier `demo/src/` ne suffit PAS. Il faut
# reconstruire et recommiter `demo/dist/`, sans quoi le déploiement servira
# l'ancienne version sans que rien ne le signale. Cf. `deploy/README.md`.
FROM nginx:1.27-alpine

# La conf remplace le `default.conf` de l'image : elle porte les en-têtes
# d'isolation cross-origin sans lesquels le wasm ne démarre pas du tout.
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

# Le site lui-même. ~33 Mo, dont 21 Mo de `blender.wasm.zst`.
COPY demo/dist/ /usr/share/nginx/html/

# Vérification au BUILD plutôt qu'à la première visite : une image sans le
# binaire Blender démarre parfaitement et rend une page morte. Mieux vaut que
# le déploiement échoue ici, avec un message qui dit quoi faire.
RUN test -f /usr/share/nginx/html/blender.wasm.zst \
 && test -f /usr/share/nginx/html/assets.tar.zst \
 && test -f /usr/share/nginx/html/index.html \
 || (echo "ERREUR : demo/dist/ est incomplet. Lancer 'cd demo && pnpm build' puis recommiter dist/." && exit 1)

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/health || exit 1
