import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root=path.dirname(fileURLToPath(import.meta.url)), app=path.dirname(root);
export default {root,resolve:{alias:{react:path.join(app,'node_modules/react'),'react-dom':path.join(app,'node_modules/react-dom')}},esbuild:{jsx:'automatic'},server:{fs:{allow:[app]}}};
