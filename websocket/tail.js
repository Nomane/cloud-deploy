// Generated by CoffeeScript 1.7.1
(function() {
  var Tail, environment, events, fs,
    __bind = function(fn, me){ return function(){ return fn.apply(me, arguments); }; },
    __hasProp = {}.hasOwnProperty,
    __extends = function(child, parent) { for (var key in parent) { if (__hasProp.call(parent, key)) child[key] = parent[key]; } function ctor() { this.constructor = child; } ctor.prototype = parent.prototype; child.prototype = new ctor(); child.__super__ = parent.prototype; return child; };

  events = require("events");

  fs = require('fs');

  environment = process.env['NODE_ENV'] || 'development';

  Tail = (function(_super) {
    __extends(Tail, _super);

    Tail.prototype.readBlock = function() {
      var block, stream;
      if (this.queue.length >= 1) {
        block = this.queue.shift();
        if (block.end > block.start) {
          stream = fs.createReadStream(this.filename, {
            start: block.start,
            end: block.end - 1,
            encoding: "utf-8"
          });
          stream.on('error', (function(_this) {
            return function(error) {
              console.log("Tail error:" + error);
              return _this.emit('error', error);
            };
          })(this));
          stream.on('end', (function(_this) {
            return function() {
              if (_this.queue.length >= 1) {
                return _this.internalDispatcher.emit("next");
              }
            };
          })(this));
          return stream.on('data', (function(_this) {
            return function(data) {
              var chunk, parts, _i, _len, _results;
              _this.buffer += data;
              parts = _this.buffer.split(_this.separator);
              _this.buffer = parts.pop();
              _results = [];
              for (_i = 0, _len = parts.length; _i < _len; _i++) {
                chunk = parts[_i];
                _results.push(_this.emit("line", chunk));
              }
              return _results;
            };
          })(this));
        }
      }
    };

    function Tail(filename, separator, frombeginning, fsWatchOptions) {
      var stats;
      this.filename = filename;
      this.separator = separator != null ? separator : '\n';
      this.frombeginning = frombeginning != null ? frombeginning : false;
      this.fsWatchOptions = fsWatchOptions != null ? fsWatchOptions : {};
      this.readBlock = __bind(this.readBlock, this);
      this.buffer = '';
      this.internalDispatcher = new events.EventEmitter();
      this.queue = [];
      this.isWatching = false;
      stats = fs.statSync(this.filename);
      this.internalDispatcher.on('next', (function(_this) {
        return function() {
          return _this.readBlock();
        };
      })(this));
      if (this.frombeginning) {
        this.pos = 0;
        this.watchEvent('change');
      } else {
        this.pos = stats.size;
      }
      this.watch();
    }

    Tail.prototype.watch = function() {
      if (this.isWatching) {
        return;
      }
      this.isWatching = true;
      if (fs.watch) {
        return this.watcher = fs.watch(this.filename, this.fsWatchOptions, (function(_this) {
          return function(e) {
            return _this.watchEvent(e);
          };
        })(this));
      } else {
        return fs.watchFile(this.filename, this.fsWatchOptions, (function(_this) {
          return function(curr, prev) {
            return _this.watchFileEvent(curr, prev);
          };
        })(this));
      }
    };

    Tail.prototype.watchEvent = function(e) {
      var stats;
      if (e === 'change') {
        stats = fs.statSync(this.filename);
        if (stats.size < this.pos) {
          this.pos = stats.size;
        }
        if (stats.size > this.pos) {
          this.queue.push({
            start: this.pos,
            end: stats.size
          });
          this.pos = stats.size;
          if (this.queue.length === 1) {
            return this.internalDispatcher.emit("next");
          }
        }
      } else if (e === 'rename') {
        this.unwatch();
        return setTimeout(((function(_this) {
          return function() {
            return _this.watch();
          };
        })(this)), 1000);
      }
    };

    Tail.prototype.watchFileEvent = function(curr, prev) {
      if (curr.size > prev.size) {
        this.queue.push({
          start: prev.size,
          end: curr.size
        });
        if (this.queue.length === 1) {
          return this.internalDispatcher.emit("next");
        }
      }
    };

    Tail.prototype.unwatch = function() {
      if (fs.watch && this.watcher) {
        this.watcher.close();
        this.pos = 0;
      } else {
        fs.unwatchFile(this.filename);
      }
      this.isWatching = false;
      return this.queue = [];
    };

    return Tail;

  })(events.EventEmitter);

  exports.Tail = Tail;

}).call(this);
