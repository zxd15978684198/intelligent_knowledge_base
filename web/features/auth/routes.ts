/**
 * 登录成功后的落地页。
 *
 * 单独一个常量而不是散在页面里：e2e（N11）与 AuthGuard（N4）都要引用同一个路径，
 * 改一处就够。
 */
export const AFTER_LOGIN_PATH = "/kbs";

/** 未登录时被送去的页面。 */
export const LOGIN_PATH = "/login";
