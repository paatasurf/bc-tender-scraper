"use strict";
var __extends = (this && this.__extends) || (function () {
    var extendStatics = function (d, b) {
        extendStatics = Object.setPrototypeOf ||
            ({ __proto__: [] } instanceof Array && function (d, b) { d.__proto__ = b; }) ||
            function (d, b) { for (var p in b) if (Object.prototype.hasOwnProperty.call(b, p)) d[p] = b[p]; };
        return extendStatics(d, b);
    };
    return function (d, b) {
        if (typeof b !== "function" && b !== null)
            throw new TypeError("Class extends value " + String(b) + " is not a constructor or null");
        extendStatics(d, b);
        function __() { this.constructor = d; }
        d.prototype = b === null ? Object.create(b) : (__.prototype = b.prototype, new __());
    };
})();
/************************************************
 *                                              *
 * /!\      GENERATED JAVASCRIPT FILE       /!\ *
 *                                              *
 * Please don't modify directly, but rather the *
 * typescript source file                       *
 *                                              *
 ************************************************/
var BaseCaptcha = /** @class */ (function () {
    function BaseCaptcha() {
    }
    BaseCaptcha.prototype.solveOnSaveButton = function (event) {
        var hdn = document.getElementById('captcha_response');
        if (!hdn.value) {
            window.ivMessage.displayLoadMsg();
            this.clickedButton = event.currentTarget;
            ivCaptcha.solve();
            return false;
        }
        return true;
    };
    BaseCaptcha.getImplementation = function () {
        var captchaType = window.ivScope.GetVar("CaptchaType", "Ivalua");
        switch (captchaType === null || captchaType === void 0 ? void 0 : captchaType.toLowerCase()) {
            case "recaptchav3":
                return new ReCaptchaV3();
            case "recaptchav2checkbox":
                return new ReCaptchaV2Checkbox();
            case "recaptchav2invisible":
                return new ReCaptchaV2Invisible();
            case "recaptchaenterpriseinvisible":
                return new ReCaptchaEnterpriseInvisible();
            case "recaptchaenterprisecheckbox":
                return new ReCaptchaEnterpriseCheckbox();
            case "ivalua":
                return new IvaluaCaptcha();
            case "none":
                return new NullCaptcha();
            default:
                throw new Error("Unknow captcha type : " + captchaType);
        }
    };
    return BaseCaptcha;
}());
var IvaluaCaptcha = /** @class */ (function (_super) {
    __extends(IvaluaCaptcha, _super);
    function IvaluaCaptcha() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    IvaluaCaptcha.prototype.init = function (config) {
        this.context = config.context;
    };
    IvaluaCaptcha.prototype.solve = function () {
        if (ivCaptcha.onSolved) {
            ivCaptcha.onSolved();
        }
        var hdn = document.getElementById('captcha_response');
        if (!hdn.value) {
            // captcha was not filled => put fake value
            hdn.value = 'no_captcha_response_provided';
        }
        if (ivCaptcha.clickedButton) {
            ivCaptcha.clickedButton.click();
            ivCaptcha.clickedButton = null;
        }
    };
    IvaluaCaptcha.prototype.regen = function () {
        window.ivCallMethod.invoke(window.ivScope.AjaxUrl('bas', 'captcha_control', null, 'methodname=RegenCaptcha'), { context: this.context }, function (response) {
            if (!response || !response.html) {
                return;
            }
            var captchaDiv = document.getElementById('captcha_display');
            if (!captchaDiv) {
                return;
            }
            captchaDiv.innerHTML = response.html;
        }, null, null);
    };
    return IvaluaCaptcha;
}(BaseCaptcha));
function reCaptchaV2InvisibleOnLoad() {
    grecaptcha.render(document.getElementsByClassName('g-recaptcha')[0], {
        callback: function (token) { ivCaptcha.executeCallBack(token); }
    }, true);
}
var ReCaptchaV2Invisible = /** @class */ (function (_super) {
    __extends(ReCaptchaV2Invisible, _super);
    function ReCaptchaV2Invisible() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    ReCaptchaV2Invisible.prototype.init = function (config) {
        // Nothing to do
    };
    ReCaptchaV2Invisible.prototype.executeCallBack = function (token) {
        var input = document.getElementById('captcha_response');
        input.value = token;
        if (ivCaptcha.onSolved) {
            ivCaptcha.onSolved();
        }
        if (ivCaptcha.clickedButton) {
            ivCaptcha.clickedButton.click();
            ivCaptcha.clickedButton = null;
        }
    };
    ReCaptchaV2Invisible.prototype.solve = function () {
        grecaptcha.execute();
    };
    return ReCaptchaV2Invisible;
}(BaseCaptcha));
function verifyCaptchaV2CheckboxResponse(token) {
    window.ivCaptcha.executeCallBack(token);
}
var ReCaptchaV2Checkbox = /** @class */ (function (_super) {
    __extends(ReCaptchaV2Checkbox, _super);
    function ReCaptchaV2Checkbox() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    ReCaptchaV2Checkbox.prototype.init = function (config) {
        // Nothing to do
    };
    ReCaptchaV2Checkbox.prototype.executeCallBack = function (token) {
        var input = document.getElementById('captcha_response');
        input.value = token;
        if (ivCaptcha.onSolved) {
            ivCaptcha.onSolved();
        }
        if (ivCaptcha.clickedButton) {
            ivCaptcha.clickedButton.click();
            ivCaptcha.clickedButton = null;
        }
    };
    ReCaptchaV2Checkbox.prototype.solveOnSaveButton = function (event) {
        var hdn = document.getElementById('captcha_response');
        if (!hdn.value) {
            window.ivMessage.AddErrorMsg(window.ivScope.GetText('/bas/captcha_control|solve_captcha'));
            return false;
        }
        return true;
    };
    ReCaptchaV2Checkbox.prototype.solve = function () {
        // Nothing to do
    };
    return ReCaptchaV2Checkbox;
}(BaseCaptcha));
var ReCaptchaV3 = /** @class */ (function (_super) {
    __extends(ReCaptchaV3, _super);
    function ReCaptchaV3() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    ReCaptchaV3.prototype.init = function (config) {
        this.publicKey = config.publicKey;
        this.action = config.action;
    };
    ReCaptchaV3.prototype.solve = function () {
        grecaptcha.ready(function () {
            var captcha = window.ivCaptcha;
            grecaptcha.execute(captcha.publicKey || '', { action: captcha.action }).then(function (token) {
                var input = document.getElementById('captcha_response');
                input.value = token;
                if (ivCaptcha.onSolved) {
                    ivCaptcha.onSolved();
                }
                if (ivCaptcha.clickedButton) {
                    ivCaptcha.clickedButton.click();
                    ivCaptcha.clickedButton = null;
                }
            });
        });
    };
    return ReCaptchaV3;
}(BaseCaptcha));
var ReCaptchaEnterpriseInvisible = /** @class */ (function (_super) {
    __extends(ReCaptchaEnterpriseInvisible, _super);
    function ReCaptchaEnterpriseInvisible() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    ReCaptchaEnterpriseInvisible.prototype.init = function (config) {
        this.publicKey = config.publicKey;
        this.action = config.action;
    };
    ReCaptchaEnterpriseInvisible.prototype.solve = function () {
        grecaptcha.enterprise.ready(function () {
            var captcha = window.ivCaptcha;
            grecaptcha.enterprise.execute(captcha.publicKey || '', { action: captcha.action }).then(function (token) {
                var input = document.getElementById('captcha_response');
                input.value = token;
                if (ivCaptcha.onSolved) {
                    ivCaptcha.onSolved();
                }
                if (ivCaptcha.clickedButton) {
                    ivCaptcha.clickedButton.click();
                    ivCaptcha.clickedButton = null;
                }
            });
        });
    };
    return ReCaptchaEnterpriseInvisible;
}(BaseCaptcha));
function verifyCaptchaEnterpriseCheckboxResponse(token) {
    window.ivCaptcha.executeCallBack(token);
}
var ReCaptchaEnterpriseCheckbox = /** @class */ (function (_super) {
    __extends(ReCaptchaEnterpriseCheckbox, _super);
    function ReCaptchaEnterpriseCheckbox() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    ReCaptchaEnterpriseCheckbox.prototype.init = function (config) {
        // Nothing to do
    };
    ReCaptchaEnterpriseCheckbox.prototype.executeCallBack = function (token) {
        var input = document.getElementById('captcha_response');
        input.value = token;
        if (ivCaptcha.onSolved) {
            ivCaptcha.onSolved();
        }
        if (ivCaptcha.clickedButton) {
            ivCaptcha.clickedButton.click();
            ivCaptcha.clickedButton = null;
        }
    };
    ReCaptchaEnterpriseCheckbox.prototype.solveOnSaveButton = function (event) {
        var hdn = document.getElementById('captcha_response');
        if (!hdn.value) {
            window.ivMessage.AddErrorMsg(window.ivScope.GetText('/bas/captcha_control|solve_captcha'));
            return false;
        }
        return true;
    };
    ReCaptchaEnterpriseCheckbox.prototype.solve = function () {
        // Nothing to do
    };
    return ReCaptchaEnterpriseCheckbox;
}(BaseCaptcha));
var NullCaptcha = /** @class */ (function (_super) {
    __extends(NullCaptcha, _super);
    function NullCaptcha() {
        return _super !== null && _super.apply(this, arguments) || this;
    }
    NullCaptcha.prototype.init = function (config) {
        // Nothing to do
    };
    NullCaptcha.prototype.solve = function () {
        // Nothing to do
    };
    NullCaptcha.prototype.solveOnSaveButton = function (event) {
        return true;
    };
    return NullCaptcha;
}(BaseCaptcha));
var ivCaptcha = BaseCaptcha.getImplementation();
