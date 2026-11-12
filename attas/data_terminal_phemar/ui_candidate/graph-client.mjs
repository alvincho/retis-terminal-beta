import {installGraphPositionBridge} from './graph-position-bridge.js';
import {operationBinding} from './neutral-operation-form.js';
import {api,compositionViewInstance} from './connected.js';
installGraphPositionBridge({instanceOf:compositionViewInstance,request:api,bindingOf:operationBinding});
