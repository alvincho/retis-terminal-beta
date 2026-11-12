/** Application code shipped with Terminal; never loaded from extension manifests. */
import {registerRenderer} from './composition-renderers.js';
import {globeHTML,mountGlobe} from './globe-view.js';
registerRenderer({id:'attas:display.globe',version:'1.0.0',features:['geojson','coordinate-rows','layers'],render:globeHTML,mount:mountGlobe});
