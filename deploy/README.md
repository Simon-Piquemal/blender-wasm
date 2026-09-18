# Déploiement — Blender wasm pour Phasen

Ce dossier sert **un seul** cas : publier le build de `demo/` sur un serveur,
pour que la fiche produit Phasen puisse ouvrir un modèle dans un onglet
(`?model=<url>&format=blend|glb`).

## Ce qui est déployé, et ce qui ne l'est pas

Le site est **construit ici, sur une machine de développement**, et versionné
dans `demo/dist/`. L'image ne fait que le servir.

Ce n'est pas un raccourci : compiler Blender en WebAssembly demande emsdk, une
chaîne CMake complète et des heures de CPU (`Makefile`,
`.github/workflows/build-release.yml`). Aucun builder d'hébergeur ne peut le
faire — il tomberait en manque de mémoire, et il le referait à chaque
déploiement pour un binaire qui ne change presque jamais.

**Conséquence à retenir** : modifier `demo/src/` ne change rien au déploiement
tant que `dist/` n'a pas été reconstruit et recommité.

```bash
cd demo && pnpm build      # ou npx vite build
git add demo/dist && git commit -m "build: …"
```

`demo/dist/assets.tar` est volontairement hors du dépôt : c'est la version non
compressée de `assets.tar.zst`, et rien ne va la chercher au runtime
(`demo/src/main.js` ne récupère que le `.zst`). L'ajouter doublerait le poids
pour un fichier jamais servi.

## Les deux en-têtes sans lesquels rien ne démarre

Blender wasm utilise des threads, donc `SharedArrayBuffer`, que les navigateurs
réservent aux pages en **isolation cross-origin** :

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

`vite.config.js` les pose pour `vite dev` et `vite preview` — **jamais en
production**. Un hébergement statique ordinaire (nginx par défaut, le mode
« Static » de Dokploy, un bucket) ne les envoie pas, et la page reste morte sans
qu'aucun journal serveur ne dise quoi que ce soit : le défaut est entièrement
côté navigateur (`SharedArrayBuffer is not defined` dans la console).

C'est la raison d'être de `deploy/nginx.conf`, et la raison pour laquelle ce
dépôt se déploie en **Dockerfile** plutôt qu'en mode statique.

## Le corollaire : CORS sur le bucket

Sous `require-corp`, l'onglet ne peut lire le modèle que si le stockage répond
en CORS. Sans `Access-Control-Allow-Origin` sur les objets de
`media/product/3d/`, le téléchargement échoue **alors que la même URL s'ouvre
parfaitement au navigateur** — c'est le piège le plus déroutant de ce montage.

C'est une configuration de bucket, pas du code.

## Réglages Dokploy

| Champ | Valeur |
|---|---|
| Provider | Github |
| Repository | `Simon-Piquemal/blender-wasm` |
| Branch | `web/numpy-gltf-ui-fixes` |
| Build Path | `/` |
| Build Type | **Dockerfile** |
| Docker File | `Dockerfile` |
| Docker Context Path | `/` |
| Port | `80` |

**Ne pas** choisir « Static » avec un Publish Directory : ce mode n'expose pas
les en-têtes d'isolation, et la page ne démarrera pas.

Une fois le domaine attribué (ex. `https://blender.phasen.fr/`), le poser côté
backend Phasen :

```
BLENDER_WEB_URL=https://blender.phasen.fr/
```

En `https` obligatoirement sur un domaine public — `SharedArrayBuffer` exige un
contexte sécurisé.

## Vérifier un déploiement

```bash
curl -sI https://<domaine>/ | grep -i cross-origin
# Cross-Origin-Opener-Policy: same-origin
# Cross-Origin-Embedder-Policy: require-corp
```

Si ces deux lignes manquent, rien d'autre ne vaut la peine d'être testé.

Ensuite, dans l'onglet : la console doit montrer le chargement de
`blender.wasm.zst` (21 Mo) puis `assets.tar.zst`. Un 404 sur l'un des deux
signifie un `dist/` incomplet — l'image refuse normalement de se construire
dans ce cas (`RUN test -f …` dans le `Dockerfile`).
