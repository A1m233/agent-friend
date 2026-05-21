/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useDispatch } from 'react-redux';
import {
  setHistoryMsg,
  setInterruptMsg,
  updateAITalkState,
  updateAIThinkState,
} from '@/store/slices/room';
import RtcClient from '@/lib/RtcClient';
import { string2tlv, tlv2String } from '@/utils/utils';

export type AnyRecord = Record<string, any>;

export enum MESSAGE_TYPE {
  BRIEF = 'conv',
  SUBTITLE = 'subv',
  FUNCTION_CALL = 'tool',
}

export enum AGENT_BRIEF {
  UNKNOWN,
  LISTENING,
  THINKING,
  SPEAKING,
  INTERRUPTED,
  FINISHED,
}

/**
 * @brief 指令类型
 */
export enum COMMAND {
  /**
   * @brief 打断指令
   */
  INTERRUPT = 'interrupt',
  /**
   * @brief 发送外部文本驱动 TTS
   */
  EXTERNAL_TEXT_TO_SPEECH = 'ExternalTextToSpeech',
  /**
   * @brief 发送外部文本驱动 LLM
   */
  EXTERNAL_TEXT_TO_LLM = 'ExternalTextToLLM',
}
/**
 * @brief 打断的类型
 */
export enum INTERRUPT_PRIORITY {
  /**
   * @brief 占位
   */
  NONE,
  /**
   * @brief 高优先级。传入信息直接打断交互，进行处理。
   */
  HIGH,
  /**
   * @brief 中优先级。等待当前交互结束后，进行处理。
   */
  MEDIUM,
  /**
   * @brief 低优先级。如当前正在发生交互，直接丢弃 Message 传入的信息。
   */
  LOW,
}

export const MessageTypeCode = {
  [MESSAGE_TYPE.SUBTITLE]: 1,
  [MESSAGE_TYPE.FUNCTION_CALL]: 2,
  [MESSAGE_TYPE.BRIEF]: 3,
};

// [voice-poc] 用统一前缀打 RTS 信令日志，方便 console 里搜
const VPOC = '[voice-poc]';

// [voice-poc] 延迟分解：记录上一次 BRIEF 状态变化的时间戳和 code，每次状态切换打印 elapsed
// 用 ===VPOC-TIMELINE=== 独立标识让用户单独搜这一个 tag 就能看到时间链路
let lastBriefTime = 0;
let lastBriefCode = -1;
// 记录每一轮 THINKING 进入时刻 + 每个 userId 是否已经在本轮见过 SUBTITLE
let thinkingTime = 0;
const subtitleFirstSeenInRound = new Set<string>();

export const useMessageHandler = () => {
  const dispatch = useDispatch();

  const maps = {
    /**
     * @brief 接收状态变化信息
     * @note https://www.volcengine.com/docs/6348/1415216?s=g
     */
    [MESSAGE_TYPE.BRIEF]: (parsed: AnyRecord) => {
      const { Stage, RoundID } = parsed || {};
      const { Code, Description } = Stage || {};
      const codeName = AGENT_BRIEF[Code] || `UNKNOWN(${Code})`;
      console.log(`${VPOC} [BRIEF] code=${Code}(${codeName}) desc=`, Description, parsed);
      if (Description === 'errorOccurred' || parsed?.ErrorInfo) {
        // [voice-poc] 火山推过来的错误事件——用独立标识 `===VPOC-ERR===` 让用户在 console 里搜这一个就够
        console.warn(`===VPOC-ERR=== ${JSON.stringify(parsed)}`);
      }
      // [voice-poc] 延迟分解：每次 BRIEF 状态变化，打印从上一个状态到当前的耗时
      // 用 ===VPOC-TIMELINE=== 独立标识，搜这一个就能看到完整时间链路
      // 统一用客户端 Date.now() 作为时间基准（BRIEF / SUBTITLE 都用同一个），
      // 避免依赖服务端 EventTime 字段（SUBTITLE 类型可能没有）
      if (Code !== lastBriefCode) {
        const now = Date.now();
        if (lastBriefTime > 0 && lastBriefCode >= 0) {
          const lastCodeName = AGENT_BRIEF[lastBriefCode] || `UNKNOWN(${lastBriefCode})`;
          const elapsed = now - lastBriefTime;
          console.log(
            `===VPOC-TIMELINE=== round=${RoundID} ${lastCodeName}(${lastBriefCode}) → ${codeName}(${Code})  +${elapsed}ms`,
          );
        } else {
          console.log(
            `===VPOC-TIMELINE=== round=${RoundID} <START> → ${codeName}(${Code})  (基准时刻)`,
          );
        }
        lastBriefTime = now;
        lastBriefCode = Code;

        // 每次进入 LISTENING / THINKING 都重置本轮的 SUBTITLE 跟踪集合
        if (Code === AGENT_BRIEF.LISTENING) {
          subtitleFirstSeenInRound.clear();
          thinkingTime = 0;
        } else if (Code === AGENT_BRIEF.THINKING) {
          thinkingTime = now;
        }
      }
      switch (Code) {
        case AGENT_BRIEF.THINKING:
          dispatch(updateAIThinkState({ isAIThinking: true }));
          break;
        case AGENT_BRIEF.SPEAKING:
          dispatch(updateAITalkState({ isAITalking: true }));
          break;
        case AGENT_BRIEF.FINISHED:
          dispatch(updateAITalkState({ isAITalking: false }));
          break;
        case AGENT_BRIEF.INTERRUPTED:
          dispatch(setInterruptMsg());
          break;
        default:
          break;
      }
    },
    /**
     * @brief 字幕
     * @note https://www.volcengine.com/docs/6348/1337284?s=g
     */
    [MESSAGE_TYPE.SUBTITLE]: (parsed: AnyRecord) => {
      const data = parsed.data?.[0] || {};
      console.log(`${VPOC} [SUBTITLE]`, data);
      if (data) {
        const { text: msg, definite, userId: user, paragraph } = data;
        // [voice-poc] TIMELINE：本轮内每个 userId 第一次出现字幕时打印延迟
        // - userId == 用户的 → ASR 流式识别第一字延迟（基准 LISTENING）
        // - userId == bot 的 → LLM 流式输出第一字延迟（基准 THINKING）
        // 用 Date.now() 客户端本地时间作为基准（与 BRIEF 一致）
        if (user && !subtitleFirstSeenInRound.has(user)) {
          subtitleFirstSeenInRound.add(user);
          const now = Date.now();
          const baseTime = thinkingTime > 0 ? thinkingTime : lastBriefTime;
          const baseLabel = thinkingTime > 0 ? 'THINKING' : 'LISTENING';
          if (baseTime > 0) {
            const elapsed = now - baseTime;
            const preview = (msg || '').slice(0, 20);
            console.log(
              `===VPOC-TIMELINE=== SUBTITLE 首字 user=${user} since ${baseLabel}: +${elapsed}ms  text="${preview}"`,
            );
          }
        }
        const isAudioEnable = RtcClient.getAgentEnabled();
        if (!isAudioEnable) {
          console.log(`${VPOC} [SUBTITLE] skipped: agent not enabled (getAgentEnabled=false)`);
        }
        if (isAudioEnable) {
          dispatch(setHistoryMsg({ text: msg, user, paragraph, definite }));
        }
      }
    },
    /**
     * @brief Function calling
     * @note https://www.volcengine.com/docs/6348/1359441?s=g
     */
    [MESSAGE_TYPE.FUNCTION_CALL]: (parsed: AnyRecord) => {
      console.log(`${VPOC} [FUNCTION_CALL]`, parsed);
      const name: string = parsed?.tool_calls?.[0]?.function?.name;
      const map: Record<string, string> = {
        getcurrentweather: '今天下雪， 最低气温零下10度',
      };

      RtcClient.engine.sendUserBinaryMessage(
        'RobotMan_',
        string2tlv(
          JSON.stringify({
            ToolCallID: parsed?.tool_calls?.[0]?.id,
            Content: map[name.toLocaleLowerCase().replaceAll('_', '')],
          }),
          'func'
        )
      );
    },
  };

  return {
    parser: (buffer: ArrayBuffer) => {
      try {
        const { type, value } = tlv2String(buffer);
        console.log(`${VPOC} [parser] type=${type} valueLen=${value?.length}`);
        maps[type as MESSAGE_TYPE]?.(JSON.parse(value));
      } catch (e) {
        console.warn(`${VPOC} [parser] error`, e);
      }
    },
  };
};
