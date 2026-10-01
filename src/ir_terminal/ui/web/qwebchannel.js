"use strict";

var QWebChannel = function(transport, initCallback) {
    if (typeof transport !== "object" || typeof transport.send !== "function") {
        console.error("The transport object must possess a 'send' function.");
        return;
    }

    var channel = this;
    this.transport = transport;
    this.execCallbacks = {};
    this.execId = 0;
    this.objects = {};

    this.send = function(data) {
        if (typeof data !== "string") {
            data = JSON.stringify(data);
        }
        channel.transport.send(data);
    };

    this.transport.onmessage = function(message) {
        var data = message.data;
        if (typeof data === "string") {
            data = JSON.parse(data);
        }
        switch (data.type) {
            case 1: // signal
                channel.handleSignal(data);
                break;
            case 2: // response
                channel.handleResponse(data);
                break;
            case 3: // property update
                channel.handlePropertyUpdate(data);
                break;
            default:
                console.error("invalid message received:", message.data);
                break;
        }
    };

    this.exec = function(data, callback) {
        if (!callback) {
            channel.send(data);
            return;
        }
        if (channel.execId === Number.MAX_VALUE) {
            channel.execId = Number.MIN_VALUE;
        }
        if (data.hasOwnProperty("id")) {
            console.error("Cannot exec message with existing id: " + JSON.stringify(data));
            return;
        }
        data.id = channel.execId++;
        channel.execCallbacks[data.id] = callback;
        channel.send(data);
    };

    this.handleSignal = function(message) {
        var object = channel.objects[message.object];
        if (object) {
            object.signalEmitted(message.signal, message.args);
        } else {
            console.warn("Unhandled signal: " + message.object + "::" + message.signal);
        }
    };

    this.handleResponse = function(message) {
        if (!message.hasOwnProperty("id")) {
            console.error("Invalid response message received: ", JSON.stringify(message));
            return;
        }
        var callback = channel.execCallbacks[message.id];
        delete channel.execCallbacks[message.id];
        if (callback) {
            callback(message.data);
        }
    };

    this.handlePropertyUpdate = function(message) {
        for (var i in message.data) {
            var data = message.data[i];
            var object = channel.objects[data.object];
            if (object) {
                object.propertyUpdate(data.signals, data.properties);
            } else {
                console.warn("Unhandled property update: " + data.object);
            }
        }
    };

    this.exec({type: 0}, function(data) {
        for (var objectName in data) {
            var object = new QObject(objectName, data[objectName], channel);
        }
        for (var objectName in channel.objects) {
            channel.objects[objectName].unwrapProperties();
        }
        if (initCallback) {
            initCallback(channel);
        }
    });
};

function QObject(name, data, webChannel) {
    this.__id__ = name;
    webChannel.objects[name] = this;

    var object = this;
    this.__objectSignals__ = {};
    this.__propertyCache__ = {};

    var createSignal = function(signalName, signalData) {
        object[signalName] = {
            connect: function(callback) {
                if (typeof callback !== "function") {
                    console.error("Bad callback given to connect to signal " + signalName);
                    return;
                }
                object.__objectSignals__[signalName] = object.__objectSignals__[signalName] || [];
                object.__objectSignals__[signalName].push(callback);
            },
            disconnect: function(callback) {
                if (typeof callback !== "function") {
                    console.error("Bad callback given to disconnect from signal " + signalName);
                    return;
                }
                if (!object.__objectSignals__[signalName]) {
                    return;
                }
                var idx = object.__objectSignals__[signalName].indexOf(callback);
                if (idx >= 0) {
                    object.__objectSignals__[signalName].splice(idx, 1);
                }
            }
        };
    };

    var createProperty = function(propertyIndex, propertyName, propertyData) {
        object.__propertyCache__[propertyIndex] = propertyData[0];
        Object.defineProperty(object, propertyName, {
            configurable: true,
            get: function () {
                return object.__propertyCache__[propertyIndex];
            },
            set: function(value) {
                if (value === void 0) {
                    console.error("Cannot set undefined value of property " + propertyName);
                    return;
                }
                object.__propertyCache__[propertyIndex] = value;
                webChannel.exec({
                    type: 4,
                    object: object.__id__,
                    property: propertyIndex,
                    value: value
                });
            }
        });
    };

    var createMethod = function(methodName, methodData) {
        object[methodName] = function() {
            var args = [];
            var callback;
            for (var i = 0; i < arguments.length; ++i) {
                if (typeof arguments[i] === "function") {
                    callback = arguments[i];
                } else {
                    args.push(arguments[i]);
                }
            }
            webChannel.exec({
                type: 3,
                object: object.__id__,
                method: methodData[0],
                args: args
            }, function(response) {
                if (callback) {
                    callback(response);
                }
            });
        };
    };

    for (var method in data.methods) {
        createMethod(method, data.methods[method]);
    }
    for (var property in data.properties) {
        createProperty(property, data.properties[property][0], data.properties[property]);
    }
    for (var signal in data.signals) {
        createSignal(signal, data.signals[signal]);
    }

    this.unwrapProperties = function() {
        for (var propertyIndex in object.__propertyCache__) {
            var val = object.__propertyCache__[propertyIndex];
            if (val && typeof val === "object" && val.hasOwnProperty("__id__")) {
                object.__propertyCache__[propertyIndex] = webChannel.objects[val.__id__];
            }
        }
    };

    this.signalEmitted = function(signalName, signalArgs) {
        var connections = object.__objectSignals__[signalName];
        if (connections) {
            connections.forEach(function(callback) {
                callback.apply(callback, signalArgs);
            });
        }
    };

    this.propertyUpdate = function(signals, properties) {
        for (var propertyIndex in properties) {
            var propertyValue = properties[propertyIndex];
            if (propertyValue && typeof propertyValue === "object" && propertyValue.hasOwnProperty("__id__")) {
                propertyValue = webChannel.objects[propertyValue.__id__];
            }
            object.__propertyCache__[propertyIndex] = propertyValue;
        }
        for (var signalName in signals) {
            object.signalEmitted(signalName, signals[signalName]);
        }
    };
}
