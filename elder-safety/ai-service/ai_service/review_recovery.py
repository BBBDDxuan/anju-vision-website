"""Review-only fall recovery used by uploaded video and live camera collectors.

Pose evidence proposes a visual review; it never diagnoses dizziness or proves
floor contact. Standalone strict alarms are unchanged. Policies were ported
from the verified local variants without runtime/experiment imports.
"""
from collections import deque
from dataclasses import dataclass, field, asdict
import math
import numpy as np

from .fall_candidates import ReviewFallDetector
from .types import Signal, finite_box, iou

REVIEW_POLICY_VERSION = "fall-recovery-v8"


def append_observed(values, item, seconds, minimum_frames=3):
    """Keep actual observations spanning time, not a fixed number of frames."""
    values.append(item)
    while len(values) > minimum_frames and values[1]['t'] <= item['t'] - seconds + 1e-8:
        values.popleft()


@dataclass(frozen=True)
class EdgePolicy:
    lower_edge_fraction:float=.02
    speed_fraction:float=.90
    minimum_hip_drop:float=.18
    minimum_shoulder_drop:float=.35
    maximum_height_ratio:float=.60
    minimum_angle:float=60.
    minimum_elapsed:float=.2
    maximum_elapsed:float=.6
    minimum_prior_upright_observations:int=2

class EdgeReviewDetector(ReviewFallDetector):
    def __init__(self,config,policy=None):
        super().__init__(config)
        self.policy=policy or EdgePolicy()
        self.frame_shape=None

    def update(self,track_id,pose,t,armed,*,recovery_allowed=True):
        result=super().update(track_id,pose,t,armed,recovery_allowed=recovery_allowed)
        if result is not None:return result
        c=self.config;p=self.policy
        if not armed or not recovery_allowed or self.frame_shape is None:return None
        h,w=self.frame_shape[:2]
        if h<=0 or w<=0 or not finite_box(pose.bbox):return None
        kp=np.asarray(pose.keypoints);core=[5,6,11,12]
        if (kp.shape!=(17,3) or not np.isfinite(kp).all() or not math.isfinite(pose.confidence)
                or pose.confidence<.35 or (kp[core,2]<c.keypoint_confidence).any()):return None
        if ((kp[core,0]<0).any() or (kp[core,0]>w).any()
                or (kp[core,1]<0).any() or (kp[core,1]>h).any()):return None
        # The new branch is deliberately unavailable with either reliable foot.
        if not (kp[[15,16],2]<c.keypoint_confidence).all():return None
        if pose.bbox[3]<h*(1-p.lower_edge_fraction):return None
        shoulder=kp[[5,6],:2].mean(0);hip=kp[[11,12],:2].mean(0)
        vec=hip-shoulder
        if np.linalg.norm(vec)<5:return None
        angle=math.degrees(math.atan2(abs(vec[0]),abs(vec[1])))
        if angle<max(p.minimum_angle,c.angle_degrees):return None
        state=self.states.get(track_id)
        if state is None or t-state.last_alert<c.cooldown_seconds:return None
        prior=[o for o in state.history if o.t<t-1e-8 and o.upright and t-o.t<=p.maximum_elapsed+1e-8]
        if len(prior)<p.minimum_prior_upright_observations:return None
        height=pose.bbox[3]-pose.bbox[1]
        for previous in prior:
            elapsed=t-previous.t
            if elapsed<p.minimum_elapsed-1e-8:continue
            hip_drop=(float(hip[1])-previous.hip[1])/previous.height
            shoulder_drop=(float(shoulder[1])-previous.shoulder[1])/previous.height
            compression=height/previous.height;speed=hip_drop/elapsed
            # Only admit a bounded near-threshold speed, with stronger other cues.
            if (hip_drop<max(c.min_hip_drop,p.minimum_hip_drop)
                    or shoulder_drop<p.minimum_shoulder_drop or compression>p.maximum_height_ratio
                    or not p.speed_fraction*c.min_drop_speed<=speed<c.min_drop_speed):continue
            state.last_alert=t;state.prone_reported=True;self.recent_alerts.append((t,pose.bbox))
            return Signal('suspected_fall',track_id,min(float(pose.confidence),float(kp[core,2].min())),{
                'algorithm':'edge_collapse_review_experiment_v1','review_required':True,
                'candidate_reason':'rapid_descent_with_unreliable_feet_at_lower_frame_edge',
                'reference_t':round(previous.t,6),'observation_seconds':round(elapsed,6),
                'hip_drop_normalized':round(hip_drop,6),'shoulder_drop_normalized':round(shoulder_drop,6),
                'hip_drop_speed_normalized':round(speed,6),'original_speed_gate':c.min_drop_speed,
                'experimental_speed_fraction':p.speed_fraction,'bbox_height_ratio':round(compression,6),
                'torso_angle_deg':round(angle,6),'frame_height':int(h),'bbox_bottom':float(pose.bbox[3]),
                'ankles_reliable':False,'motion_transition_observed':True,'floor_contact_observed':False,
                'confidence_kind':'minimum_pose_and_core_score_not_calibrated_event_probability',
                'limitation':'Experimental uncertain motion candidate near image boundary. Cropping and missing feet do not prove a fall; visual review required.'})
        return None

@dataclass(frozen=True)
class TemporalPolicy:
    straightness_minimum:float=.90
    confirmation_seconds:float=.40
    minimum_confirmation_frames:int=3
    horizontal_slope_maximum:float=.55
    minimum_body_span_pixels:float=20.

def body_projection(pose,shape,threshold,policy):
    k=np.asarray(pose.keypoints);ids=[5,6,11,12,15,16]
    if (shape is None or len(shape)<2 or min(shape[:2])<=0 or k.shape!=(17,3)
            or not finite_box(pose.bbox) or not math.isfinite(pose.confidence) or pose.confidence<.35
            or not np.isfinite(k[ids]).all() or (k[ids,2]<threshold).any()):return None
    h,w=shape[:2]
    if ((k[ids,0]<0).any() or (k[ids,0]>w).any() or (k[ids,1]<0).any() or (k[ids,1]>h).any()):return None
    shoulder=k[[5,6],:2].mean(0);hip=k[[11,12],:2].mean(0);feet=k[[15,16],:2].mean(0)
    torso=hip-shoulder;legs=feet-hip;whole=feet-shoulder
    a=float(np.linalg.norm(torso));b=float(np.linalg.norm(legs));span=float(np.linalg.norm(whole))
    if a<5 or b<5 or span<policy.minimum_body_span_pixels:return None
    straightness=span/(a+b)
    if straightness<policy.straightness_minimum:return None
    inverted=shoulder[1]>hip[1]+5 and hip[1]>feet[1]+5
    horizontal=abs(whole[1])<=policy.horizontal_slope_maximum*abs(whole[0]) and abs(whole[0])>=policy.minimum_body_span_pixels
    # Same-direction segments put the hip between the body endpoints, rejecting a fold.
    if float(np.dot(torso,whole))<=0 or float(np.dot(legs,whole))<=0:return None
    if not inverted and not horizontal:return None
    return {'projection':'inverted_order' if inverted else 'horizontal_order',
            'straightness':round(straightness,6),'body_span_pixels':round(span,6),
            'confidence':min(float(pose.confidence),float(k[ids,2].min()))}

class HackathonReview(EdgeReviewDetector):
    def __init__(self,config,*,recover=True,confirm=True,policy=None):
        super().__init__(config);self.temporal_policy=policy or TemporalPolicy()
        self.recover_enabled=recover;self.confirm_enabled=confirm
        self.body_holds={};self.pending_lateral={};self.audit=[]

    def reset(self):
        super().reset();self.body_holds.clear();self.pending_lateral.clear()

    def retain(self,present,*,recoverable=None):
        super().retain(present,recoverable=recoverable)
        # No body hold or pending transition crosses any missing-person sample.
        self.body_holds={k:v for k,v in self.body_holds.items() if k in present}
        self.pending_lateral={k:v for k,v in self.pending_lateral.items() if k in present}

    def _publish(self,track_id,pose,t,confidence,details):
        s=self.states.get(track_id)
        if s is None or t-s.last_alert<self.config.cooldown_seconds:return None
        s.last_alert=t;s.prone_reported=True;self.recent_alerts.append((t,pose.bbox))
        return Signal('suspected_fall',track_id,confidence,details)

    def update(self,track_id,pose,t,armed,*,recovery_allowed=True):
        c=self.config;p=self.temporal_policy
        state=self.states.get(track_id)
        old_alert=state.last_alert if state else -math.inf
        old_reported=state.prone_reported if state else False
        prior_alerts=list(self.recent_alerts)
        signal=super().update(track_id,pose,t,armed,recovery_allowed=recovery_allowed)
        if signal is not None and self.confirm_enabled and signal.details.get('candidate_reason')=='rapid_lateral_rotation_and_body_collapse':
            # Parent marked its proposal published. Roll back only these publication
            # fields while the transition awaits an observed whole-body tail.
            state=self.states.get(track_id)
            if state:state.last_alert=old_alert;state.prone_reported=old_reported
            self.recent_alerts=deque(prior_alerts,maxlen=64)
            if track_id not in self.pending_lateral:
                self.pending_lateral[track_id]={'t':t,'signal':signal}
                self.audit.append({'t':t,'track_id':track_id,'action':'pending_lateral'})
            signal=None
        elif signal is not None:
            self.pending_lateral.pop(track_id,None)
        geometry=body_projection(pose,self.frame_shape,c.keypoint_confidence,p) if recovery_allowed else None
        if geometry is None:
            self.body_holds.pop(track_id,None)
            hold=None
        else:
            hold=self.body_holds.get(track_id)
            # Missing/invalid geometry resets observed duration; never interpolate.
            if hold is None or t<=hold['last_t'] or t-hold['last_t']>min(.2,c.max_gap_seconds)+1e-8:
                hold={'since':t,'last_t':t,'frames':0,'confidence':geometry['confidence']}
                self.body_holds[track_id]=hold
            hold['last_t']=t;hold['frames']+=1;hold['confidence']=min(hold['confidence'],geometry['confidence'])
        pending=self.pending_lateral.get(track_id)
        if not recovery_allowed:
            self.pending_lateral.pop(track_id,None);pending=None
        if pending and t-pending['t']>c.observation_seconds+1e-8:
            self.audit.append({'t':t,'track_id':track_id,'action':'expired_lateral','trigger_t':pending['t']})
            self.pending_lateral.pop(track_id,None);pending=None
        if signal is not None:return signal
        if pending and hold and hold['frames']>=p.minimum_confirmation_frames and t-hold['since']+1e-8>=p.confirmation_seconds:
            original=pending['signal'];details=dict(original.details)
            details.update(algorithm='body_confirmed_lateral_review_experiment_v1',
                original_trigger_t=pending['t'],confirmation_delay_seconds=round(t-pending['t'],6),
                observed_body_hold_seconds=round(t-hold['since'],6),observed_body_frames=hold['frames'],
                body_projection=geometry,review_required=True,floor_contact_observed=False,
                limitation='Observed whole-body projection after a rapid lateral proposal, not confirmed floor contact. Visual review required.')
            result=self._publish(track_id,pose,t,min(original.confidence,hold['confidence']),details)
            if result:self.pending_lateral.pop(track_id,None)
            return result
        state=self.states.get(track_id)
        if (self.recover_enabled and hold and state and not state.prone_reported
                and hold['frames']>=3 and t-hold['since']+1e-8>=max(.8,c.hold_seconds)):
            result=self._publish(track_id,pose,t,hold['confidence'],{
                'algorithm':'ordered_body_projection_review_experiment_v1','review_required':True,
                'candidate_reason':'sustained_aligned_body_projection_without_observed_transition',
                'fall_transition_observed':False,'floor_contact_observed':False,
                'projection':geometry['projection'],'body_straightness':geometry['straightness'],
                'hold_seconds':round(t-hold['since'],6),'frames':hold['frames'],
                'confidence_kind':'minimum_observed_pose_and_required_joint_score_not_event_probability',
                'limitation':'Continuous aligned body projection only. Does not prove descent, floor contact or accidental fall; voluntary lying, handstands and camera orientation need visual review.'})
            if result:self.pending_lateral.pop(track_id,None)
            return result
        return None

@dataclass
class Context:
    references:deque=field(default_factory=lambda:deque(maxlen=32))
    upright_since:float|None=None
    upright_frames:int=0
    last_full_t:float|None=None
    last_bbox:tuple|None=None
    tail:deque=field(default_factory=deque)
    gaps:int=0

class DescentReview(HackathonReview):
    max_unknown_seconds=.2
    def __init__(self,c):
        super().__init__(c,recover=True,confirm=False)
        self.context={};self.now=0.
    def reset(self):super().reset();self.context.clear()
    def retain(self,present,*,recoverable=None):
        super().retain(present,recoverable=recoverable)
        alive=present if recoverable is None else present|recoverable
        self.context={k:s for k,s in self.context.items() if k in present or
            (k in alive and s.last_full_t is not None and 0<=self.now-s.last_full_t<=self.max_unknown_seconds+1e-8)}
        for k,s in self.context.items():
            if k not in present:
                s.tail.clear();s.upright_since=None;s.upright_frames=0;s.gaps+=1

    def update(self,key,pose,t,armed,*,recovery_allowed=True):
        original=super().update(key,pose,t,armed,recovery_allowed=recovery_allowed)
        c=self.config;s=self.context.setdefault(key,Context());kp=np.asarray(pose.keypoints);ids=[5,6,11,12,15,16]
        if not recovery_allowed or not finite_box(pose.bbox) or not math.isfinite(pose.confidence):
            self.context.pop(key,None);return original
        if s.last_bbox is not None and iou(pose.bbox,s.last_bbox)<.3:
            s=self.context[key]=Context()
        s.last_bbox=pose.bbox
        reliable=(kp.shape==(17,3) and np.isfinite(kp[ids]).all() and (kp[ids,2]>=c.keypoint_confidence).all() and pose.confidence>=.35)
        if reliable:
            shoulder=kp[[5,6],:2].mean(0);hip=kp[[11,12],:2].mean(0)
            reliable=float(np.linalg.norm(hip-shoulder))>=5
        if not reliable:
            s.tail.clear();s.upright_since=None;s.upright_frames=0;s.gaps+=1
            if s.last_full_t is None or t-s.last_full_t>self.max_unknown_seconds+1e-8:self.context.pop(key,None)
            return original
        if s.last_full_t is not None and (t<=s.last_full_t or t-s.last_full_t>self.max_unknown_seconds+1e-8):
            s=self.context[key]=Context(last_bbox=pose.bbox)
        s.last_full_t=t
        shape=self.frame_shape
        if shape is None:return original
        h,w=shape[:2]
        if ((kp[ids,0]<0).any() or (kp[ids,0]>w).any() or (kp[ids,1]<0).any() or (kp[ids,1]>h).any()):
            self.context.pop(key,None);return original
        vec=hip-shoulder;height=pose.bbox[3]-pose.bbox[1]
        angle=math.degrees(math.atan2(abs(vec[0]),abs(vec[1])))
        upright=vec[1]>0 and angle<=c.upright_angle_degrees and (pose.bbox[2]-pose.bbox[0])/height<c.min_bbox_ratio
        if upright:
            if s.upright_since is None:s.upright_since=t;s.upright_frames=0
            s.upright_frames+=1
            if t-s.upright_since+1e-8>=c.min_upright_seconds and s.upright_frames>=3:
                s.references.append({'t':t,'hip':hip.copy(),'shoulder':shoulder.copy(),'height':height,'gaps':s.gaps,
                    'confidence':min(float(pose.confidence),float(kp[ids,2].min()))})
        else:s.upright_since=None;s.upright_frames=0
        while s.references and t-s.references[0]['t']>c.observation_seconds+1e-8:s.references.popleft()
        geometry=body_projection(pose,shape,c.keypoint_confidence,TemporalPolicy())
        if geometry is None or geometry['projection']!='inverted_order':s.tail.clear()
        else:append_observed(s.tail,{'t':t,'confidence':geometry['confidence']},.2)
        if original is not None:return original
        if len(s.tail)<3 or s.tail[-1]['t']-s.tail[0]['t']<.2-1e-8:return None
        # Only directly observed, ordered full-body poses form the fresh tail.
        for ref in reversed(s.references):
            dt=t-ref['t']
            if dt<.2-1e-8:continue
            hip_drop=float((hip[1]-ref['hip'][1])/ref['height'])
            shoulder_drop=float((shoulder[1]-ref['shoulder'][1])/ref['height'])
            speed=float(np.linalg.norm(hip-ref['hip'])/ref['height']/dt)
            if hip_drop<max(.18,c.min_hip_drop) or shoulder_drop<.22 or speed<c.min_drop_speed:continue
            return self._publish(key,pose,t,min(ref['confidence'],*(r['confidence'] for r in s.tail)),{
                'algorithm':'observed_descent_to_inverted_review_v1','review_required':True,
                'candidate_reason':'observed_descent_with_fresh_inverted_tail_after_short_pose_gaps',
                'reference_t':round(ref['t'],6),'elapsed_seconds':round(dt,6),
                'hip_drop_normalized':round(hip_drop,6),'shoulder_drop_normalized':round(shoulder_drop,6),
                'hip_motion_speed_normalized':round(speed,6),'fresh_full_body_frames':len(s.tail),
                'fresh_body_observation_seconds':round(s.tail[-1]['t']-s.tail[0]['t'],6),
                'unknown_sample_count_since_reference':s.gaps-ref['gaps'],
                'maximum_unknown_gap_seconds':self.max_unknown_seconds,
                'descent_evidence_observed':True,'complete_transition_observed':False,'floor_contact_observed':False,
                'confidence_kind':'minimum_observed_pose_and_joint_scores_not_event_probability',
                'limitation':'Partial observed descent and a fresh inverted body tail on one unambiguous track. Unknown samples are not interpolated or counted as a continuous prone hold; visual review required.'})
        return None

@dataclass(frozen=True)
class SeatPolicy:
    stable_seconds:float=.2
    stable_frames:int=3
    minimum_knee_angle:float=45.
    maximum_knee_angle:float=125.
    minimum_hip_angle:float=45.
    maximum_hip_angle:float=135.
    maximum_thigh_vertical_torso_ratio:float=.75
    maximum_seated_torso_angle:float=45.
    stable_hip_motion:float=.08
    stable_shoulder_motion:float=.10
    rising_hip_lift:float=.04
    rising_knee_extension:float=20.
    minimum_hip_displacement:float=.25
    minimum_shoulder_displacement:float=.35
    minimum_hip_drop:float=.12
    minimum_shoulder_drop:float=.15
    fresh_tail_frames:int=3
    fresh_tail_seconds:float=.2
    core_shoulder_drop:float=.15
    core_hip_drop:float=.08

def joint_angle(a,b,c):
    u=a-b;v=c-b;den=float(np.linalg.norm(u)*np.linalg.norm(v))
    if den<1e-8:return None
    return math.degrees(math.acos(float(np.clip(np.dot(u,v)/den,-1,1))))

def observation(pose,shape,confidence,policy):
    k=np.asarray(pose.keypoints)
    if (shape is None or k.shape!=(17,3) or not finite_box(pose.bbox) or not math.isfinite(pose.confidence)
            or pose.confidence<.35 or not np.isfinite(k).all() or (k[[5,6,11,12],2]<confidence).any()):return None
    h,w=shape[:2];core=k[[5,6,11,12]]
    if ((core[:,0]<0).any() or (core[:,0]>w).any() or (core[:,1]<0).any() or (core[:,1]>h).any()):return None
    shoulder=k[[5,6],:2].mean(0);hip=k[[11,12],:2].mean(0);v=hip-shoulder;torso=float(np.linalg.norm(v))
    if torso<5:return None
    torso_angle=math.degrees(math.atan2(abs(v[0]),abs(v[1])))
    legs=[]
    for s,j,n,a in [(5,11,13,15),(6,12,14,16)]:
        ids=[s,j,n,a]
        if (k[ids,2]<confidence).any():continue
        if ((k[ids,0]<0).any() or (k[ids,0]>w).any() or (k[ids,1]<0).any() or (k[ids,1]>h).any()):continue
        knee=joint_angle(k[j,:2],k[n,:2],k[a,:2]);hip_angle=joint_angle(k[s,:2],k[j,:2],k[n,:2])
        if knee is None or hip_angle is None:continue
        seated=(policy.minimum_knee_angle<=knee<=policy.maximum_knee_angle
            and policy.minimum_hip_angle<=hip_angle<=policy.maximum_hip_angle
            and abs(float(k[n,1]-k[j,1]))<=policy.maximum_thigh_vertical_torso_ratio*torso
            and float(k[a,1]-k[n,1])>=5)
        legs.append({'knee':knee,'seated':seated,'confidence':float(k[ids,2].min())})
    return {'shoulder':shoulder,'hip':hip,'height':pose.bbox[3]-pose.bbox[1],
        'confidence':min(float(pose.confidence),float(core[:,2].min())),
        'seated_proxy':bool(v[1]>0 and torso_angle<=policy.maximum_seated_torso_angle and any(l['seated'] for l in legs)),
        'knee_extension':max((l['knee'] for l in legs),default=None)}

@dataclass
class SeatState:
    observations:deque=field(default_factory=deque)
    origin:dict|None=None
    peak:dict|None=None
    last_seated_t:float|None=None
    last_core_t:float|None=None
    last_box:tuple|None=None
    rising:bool=False
    tail:deque=field(default_factory=deque)
    core_tail:deque=field(default_factory=deque)
    unknown_samples:int=0

class SeatedReview(DescentReview):
    def __init__(self,c,policy=None):super().__init__(c);self.seat_policy=policy or SeatPolicy();self.seat_states={}
    def reset(self):super().reset();self.seat_states.clear()
    def retain(self,present,*,recoverable=None):
        super().retain(present,recoverable=recoverable)
        alive=present if recoverable is None else present|recoverable
        self.seat_states={k:s for k,s in self.seat_states.items() if k in present or (k in alive and s.last_core_t is not None and 0<=self.now-s.last_core_t<=self.config.max_gap_seconds+1e-8)}
        for key,s in self.seat_states.items():
            if key not in present:s.observations.clear();s.tail.clear();s.core_tail.clear();s.unknown_samples+=1

    def update(self,key,pose,t,armed,*,recovery_allowed=True):
        parent=super().update(key,pose,t,armed,recovery_allowed=recovery_allowed)
        c=self.config;p=self.seat_policy;s=self.seat_states.setdefault(key,SeatState())
        if not recovery_allowed or not finite_box(pose.bbox):self.seat_states.pop(key,None);return parent
        if (s.last_box is not None and iou(pose.bbox,s.last_box)<.3) or (s.last_core_t is not None and (t<=s.last_core_t or t-s.last_core_t>c.max_gap_seconds+1e-8)):
            s=self.seat_states[key]=SeatState()
        s.last_box=pose.bbox
        o=observation(pose,self.frame_shape,c.keypoint_confidence,p)
        if o is None:
            s.observations.clear();s.tail.clear();s.core_tail.clear();s.unknown_samples+=1
            if s.last_core_t is None or t-s.last_core_t>c.max_gap_seconds+1e-8:self.seat_states.pop(key,None)
            return parent
        s.last_core_t=t;o={**o,'t':t}
        if o['seated_proxy']:
            append_observed(s.observations,o,p.stable_seconds,p.stable_frames)
            if len(s.observations)>=p.stable_frames and t-s.observations[0]['t']+1e-8>=p.stable_seconds:
                first=s.observations[0]
                stable=(all(np.linalg.norm(a['hip']-first['hip'])/first['height']<=p.stable_hip_motion for a in s.observations)
                    and all(np.linalg.norm(a['shoulder']-first['shoulder'])/first['height']<=p.stable_shoulder_motion for a in s.observations))
                if stable:
                    s.last_seated_t=t
                    if s.origin is None:
                        s.origin={**o};s.peak={**o};s.rising=False
                    elif not s.rising and np.linalg.norm(o['hip']-s.origin['hip'])/s.origin['height']<=p.stable_hip_motion:
                        s.origin={**o};s.peak={**o}
        else:s.observations.clear()
        if s.origin is not None:
            if t-max(s.last_seated_t or s.origin['t'],s.peak['t'])>c.observation_seconds+1e-8:
                s.origin=None;s.peak=None;s.rising=False;s.tail.clear()
            else:
                lift=float((s.origin['hip'][1]-o['hip'][1])/s.origin['height'])
                extension=(o['knee_extension']-s.origin['knee_extension']) if o['knee_extension'] is not None and s.origin['knee_extension'] is not None else 0
                if lift>=p.rising_hip_lift and extension>=p.rising_knee_extension:s.rising=True
                # Follow only upward hip progress; never lower the anchor during collapse.
                if o['hip'][1]<s.peak['hip'][1]:s.peak={**o}
        if s.core_tail and t-s.core_tail[-1]['t']>min(.2,c.max_gap_seconds)+1e-8:
            s.core_tail.clear()
        append_observed(s.core_tail,o,p.fresh_tail_seconds,p.fresh_tail_frames)
        g=body_projection(pose,self.frame_shape,c.keypoint_confidence,TemporalPolicy())
        if g is None:s.tail.clear()
        else:
            if s.tail and t-s.tail[-1]['t']>min(.2,c.max_gap_seconds)+1e-8:s.tail.clear()
            append_observed(s.tail,{'t':t,'confidence':g['confidence']},p.fresh_tail_seconds,p.fresh_tail_frames)
        if parent is not None:return parent
        # A proposal needs observed core descent, not a straight, fully visible
        # body on the floor. Keep the seated/rising reference and three fresh
        # reliable shoulder/hip observations; missing feet do not veto review.
        if s.origin is not None and len(s.core_tail)>=p.fresh_tail_frames:
            ref=s.peak if s.rising else s.origin
            span=s.core_tail[-1]['t']-s.core_tail[0]['t']
            if (span>=p.fresh_tail_seconds-1e-8
                    and all(.2-1e-8<=a['t']-ref['t']<=c.observation_seconds+1e-8
                            and (a['shoulder'][1]-ref['shoulder'][1])/ref['height']>=p.core_shoulder_drop
                            and (a['hip'][1]-ref['hip'][1])/ref['height']>=p.core_hip_drop
                            for a in s.core_tail)):
                return self._publish(key,pose,t,min(ref['confidence'],*(a['confidence'] for a in s.core_tail)),{
                    'algorithm':'seated_core_descent_review_v1','review_required':True,
                    'candidate_reason':'seated_or_partial_rise_then_observed_shoulder_and_hip_descent',
                    'preceding_phase':'rising_from_seated_proxy' if s.rising else 'seated_proxy',
                    'reference_t':round(ref['t'],6),'elapsed_seconds':round(t-ref['t'],6),
                    'shoulder_drop_normalized':round(float((o['shoulder'][1]-ref['shoulder'][1])/ref['height']),6),
                    'hip_drop_normalized':round(float((o['hip'][1]-ref['hip'][1])/ref['height']),6),
                    'fresh_core_frames':len(s.core_tail),'fresh_core_span_seconds':round(span,6),
                    'whole_body_required':False,'standing_required':False,'chair_verified':False,
                    'fall_transition_observed':False,'descent_observed':True,'floor_contact_observed':False,
                    'confidence_kind':'minimum_observed_pose_and_core_scores_not_event_probability',
                    'limitation':'Recall-oriented motion proposal. Normal sitting, kneeling or voluntary lowering can match; visual review must distinguish them. Folded legs and unreliable feet are allowed; no fall or floor contact is confirmed.'})
        if s.origin is None or len(s.tail)<p.fresh_tail_frames or s.tail[-1]['t']-s.tail[0]['t']<p.fresh_tail_seconds-1e-8:return None
        ref=s.peak if s.rising else s.origin;dt=t-ref['t']
        if not .2-1e-8<=dt<=c.observation_seconds+1e-8:return None
        hip_shift=float(np.linalg.norm(o['hip']-ref['hip'])/ref['height']);shoulder_shift=float(np.linalg.norm(o['shoulder']-ref['shoulder'])/ref['height'])
        hip_drop=float((o['hip'][1]-ref['hip'][1])/ref['height']);shoulder_drop=float((o['shoulder'][1]-ref['shoulder'][1])/ref['height'])
        speed=hip_shift/dt
        if (hip_shift<p.minimum_hip_displacement or shoulder_shift<p.minimum_shoulder_displacement
                or speed<c.min_drop_speed or not (hip_drop>=p.minimum_hip_drop or shoulder_drop>=p.minimum_shoulder_drop)):return None
        return self._publish(key,pose,t,min(s.origin['confidence'],ref['confidence'],*(x['confidence'] for x in s.tail)),{
            'algorithm':'seated_or_rising_collapse_review_v1','review_required':True,
            'candidate_reason':'seated_or_partial_rise_then_large_motion_and_abnormal_body_tail',
            'preceding_phase':'rising_from_seated_proxy' if s.rising else 'seated_proxy',
            'standing_required':False,'chair_verified':False,'seated_context_t':round(s.origin['t'],6),'motion_reference_t':round(ref['t'],6),
            'hip_displacement_normalized':round(hip_shift,6),'shoulder_displacement_normalized':round(shoulder_shift,6),
            'hip_drop_normalized':round(hip_drop,6),'shoulder_drop_normalized':round(shoulder_drop,6),
            'hip_motion_speed_normalized':round(speed,6),'body_projection':g['projection'],
            'fresh_body_frames':len(s.tail),'fresh_body_span_seconds':round(s.tail[-1]['t']-s.tail[0]['t'],6),
            'unknown_samples_in_context':s.unknown_samples,'fall_transition_observed':False,'floor_contact_observed':False,
            'confidence_kind':'minimum_observed_pose_and_joint_scores_not_fall_probability',
            'limitation':'Seat-like or bent-leg posture proxy, not a verified chair. Large observed movement and a fresh reliable abnormal body tail require visual review; no completed standing, continuous fall or floor contact is assumed.'})

@dataclass(frozen=True)
class ForwardEdgePolicy:
    edge_fraction:float=.02
    minimum_hip_drop:float=.12
    minimum_shoulder_drop:float=.35
    maximum_torso_vertical_ratio:float=.40
    minimum_recent_shoulder_motion:float=.10
    recent_frames:int=3
    recent_seconds:float=.20
    reference_seconds:float=.80

class ForwardEdgeReview(SeatedReview):
    def __init__(self,c,policy=None):super().__init__(c);self.forward_policy=policy or ForwardEdgePolicy()
    def update(self,key,pose,t,armed,*,recovery_allowed=True):
        parent=super().update(key,pose,t,armed,recovery_allowed=recovery_allowed)
        if parent is not None:return parent
        c=self.config;p=self.forward_policy;k=np.asarray(pose.keypoints);shape=self.frame_shape
        if not armed or not recovery_allowed or shape is None or not finite_box(pose.bbox):return None
        h,w=shape[:2];ids=[5,6,11,12]
        if (k.shape!=(17,3) or not np.isfinite(k).all() or not math.isfinite(pose.confidence)
                or pose.confidence<.35 or (k[ids,2]<c.keypoint_confidence).any()):return None
        if ((k[ids,0]<0).any() or (k[ids,0]>w).any() or (k[ids,1]<0).any() or (k[ids,1]>h).any()):return None
        # Boundary/weak feet are visibility qualifiers; motion must be observed.
        if pose.bbox[3]<h*(1-p.edge_fraction) or (k[[15,16],2]>=c.keypoint_confidence).all():return None
        s=self.states.get(key)
        if s is None or t-s.last_alert<c.cooldown_seconds or len(s.history)<p.recent_frames:return None
        tail=deque()
        for item in s.history:
            append_observed(tail,{'t':item.t,'observation':item},p.recent_seconds,p.recent_frames)
        tail=[item['observation'] for item in tail]
        if tail[-1].t-tail[0].t<p.recent_seconds-1e-8 or any(not 0<b.t-a.t<=.2+1e-8 for a,b in zip(tail,tail[1:])):return None
        if any(b.shoulder[1]<a.shoulder[1] for a,b in zip(tail,tail[1:])):return None
        current=tail[-1]
        for ref in s.history:
            elapsed=t-ref.t
            if not ref.upright or ref.ankle is None or not p.recent_seconds-1e-8<=elapsed<=p.reference_seconds+1e-8:continue
            if any(not (0<=o.shoulder[0]<=w and 0<=o.hip[0]<=w and 0<=o.shoulder[1]<=h and 0<=o.hip[1]<=h) for o in [ref,*tail]):continue
            hip_drop=(current.hip[1]-ref.hip[1])/ref.height
            shoulder_drop=(current.shoulder[1]-ref.shoulder[1])/ref.height
            span=abs(current.hip[1]-current.shoulder[1])/max(abs(ref.hip[1]-ref.shoulder[1]),5.)
            recent=(current.shoulder[1]-tail[0].shoulder[1])/ref.height
            speed=shoulder_drop/elapsed
            tilted=current.angle>=c.angle_degrees or current.shoulder[1]>current.hip[1]
            if (hip_drop<max(c.min_hip_drop,p.minimum_hip_drop) or shoulder_drop<p.minimum_shoulder_drop
                    or speed<c.min_drop_speed or span>p.maximum_torso_vertical_ratio
                    or recent<p.minimum_recent_shoulder_motion or not tilted):continue
            return self._publish(key,pose,t,min(float(pose.confidence),float(k[ids,2].min())),{
                'algorithm':'observed_forward_edge_collapse_review_v1','review_required':True,
                'candidate_reason':'observed_rapid_forward_collapse_at_lower_frame_boundary',
                'reference_t':round(ref.t,6),'elapsed_seconds':round(elapsed,6),
                'hip_drop_normalized':round(hip_drop,6),'shoulder_drop_normalized':round(shoulder_drop,6),
                'shoulder_drop_speed_normalized':round(speed,6),'torso_vertical_ratio':round(span,6),
                'recent_reliable_core_frames':len(tail),'recent_core_span_seconds':round(t-tail[0].t,6),
                'bbox_bottom_fraction':round(pose.bbox[3]/h,6),'ankles_reliable':False,
                'motion_observed':True,'floor_contact_observed':False,'complete_transition_observed':False,
                'confidence_kind':'minimum_pose_and_core_scores_not_event_probability',
                'limitation':'Observed rapid shoulder/hip descent and torso foreshortening near boundary; requires visual review. Missing feet or later disappearance alone are not fall evidence.'})
        return None

@dataclass(frozen=True)
class SlowPolicy:
    background_seconds:float=.5
    background_motion:float=.06
    window_seconds:float=6.
    minimum_descent_seconds:float=1.5
    low_hold_seconds:float=1.
    minimum_low_frames:int=5
    minimum_hip_drop:float=.30
    minimum_seated_hip_drop:float=.25
    minimum_shoulder_drop:float=.40
    maximum_hip_foot_span:float=.20
    maximum_shoulder_foot_span:float=.35
    maximum_shoulder_speed:float=.8
    maximum_sample_gap:float=.2

@dataclass
class SlowState:
    history:deque=field(default_factory=lambda:deque(maxlen=256))
    references:deque=field(default_factory=lambda:deque(maxlen=128))
    background:deque=field(default_factory=lambda:deque(maxlen=64))
    box:tuple|None=None

class SlowReview(ForwardEdgeReview):
    def __init__(self,c,policy=None):super().__init__(c);self.slow_policy=policy or SlowPolicy();self.slow_states={}
    def reset(self):super().reset();self.slow_states.clear()
    def retain(self,present,*,recoverable=None):
        super().retain(present,recoverable=recoverable)
        # Unknown time is never counted as observed descent or a low-posture hold.
        self.slow_states={k:v for k,v in self.slow_states.items() if k in present}
    def _low(self,ref,o):
        p=self.slow_policy;h=ref['height'];foot=o['feet'][1]
        minimum=p.minimum_seated_hip_drop if ref['base']=='seated' else p.minimum_hip_drop
        return ((o['hip'][1]-ref['hip'][1])/h>=minimum
            and (o['shoulder'][1]-ref['shoulder'][1])/h>=p.minimum_shoulder_drop
            and (o['angle']>=60 or o['shoulder'][1]>=o['hip'][1]+5)
            and -.05<=(foot-o['hip'][1])/h<=p.maximum_hip_foot_span
            and -.05<=(foot-o['shoulder'][1])/h<=p.maximum_shoulder_foot_span)
    def update(self,key,pose,t,armed,*,recovery_allowed=True):
        parent=super().update(key,pose,t,armed,recovery_allowed=recovery_allowed)
        c=self.config;p=self.slow_policy;shape=self.frame_shape
        o=observation(pose,shape,c.keypoint_confidence,SeatPolicy()) if recovery_allowed else None
        k=np.asarray(pose.keypoints)
        if o is None or (k[[15,16],2]<c.keypoint_confidence).any():
            self.slow_states.pop(key,None);return parent
        h,w=shape[:2];feet=k[[15,16],:2].mean(0)
        if ((k[[15,16],0]<0).any() or (k[[15,16],0]>w).any() or (k[[15,16],1]<0).any() or (k[[15,16],1]>h).any()):
            self.slow_states.pop(key,None);return parent
        v=o['hip']-o['shoulder'];angle=math.degrees(math.atan2(abs(v[0]),abs(v[1])))
        base=('seated' if o['seated_proxy'] else 'standing' if v[1]>0 and angle<=c.upright_angle_degrees and o['knee_extension'] is not None and o['knee_extension']>=145 else None)
        o={**o,'t':t,'feet':feet,'angle':angle,'base':base,'confidence':min(o['confidence'],float(k[[15,16],2].min()))}
        state=self.slow_states.setdefault(key,SlowState())
        if state.history and (not 0<t-state.history[-1]['t']<=p.maximum_sample_gap+1e-8 or iou(pose.bbox,state.box)<.3):
            state=self.slow_states[key]=SlowState()
        state.box=pose.bbox;state.history.append(o)
        for values in [state.history,state.references]:
            while values and t-values[0]['t']>p.window_seconds+1e-8:values.popleft()
        if base is None or (state.background and state.background[-1]['base']!=base):state.background.clear()
        if base is not None:
            append_observed(state.background,o,p.background_seconds)
            if len(state.background)>=3 and t-state.background[0]['t']>=p.background_seconds-1e-8:
                first=state.background[0]
                if all(max(np.linalg.norm(a['hip']-first['hip']),np.linalg.norm(a['shoulder']-first['shoulder']))/first['height']<=p.background_motion for a in state.background):
                    state.references.append(o)
        if parent is not None:return parent
        tail=[a for a in state.history if a['t']>=t-p.low_hold_seconds-.05]
        if len(tail)<p.minimum_low_frames or t-tail[0]['t']<p.low_hold_seconds-1e-8:return None
        for ref in state.references:
            elapsed=tail[0]['t']-ref['t']
            if elapsed<p.minimum_descent_seconds or not all(self._low(ref,a) for a in tail):continue
            motion=[a for a in state.history if ref['t']<=a['t']<=tail[0]['t']]
            if len(motion)<3:continue
            peak_hip=max(max(0,b['hip'][1]-a['hip'][1])/ref['height']/(b['t']-a['t']) for a,b in zip(motion,motion[1:]))
            peak_shoulder=max(max(0,b['shoulder'][1]-a['shoulder'][1])/ref['height']/(b['t']-a['t']) for a,b in zip(motion,motion[1:]))
            if peak_hip>c.min_drop_speed or peak_shoulder>p.maximum_shoulder_speed:continue
            return self._publish(key,pose,t,min(ref['confidence'],*(a['confidence'] for a in motion),*(a['confidence'] for a in tail)),{
                'algorithm':'slow_descent_low_posture_review_v1','review_required':True,
                'candidate_reason':'observed_slow_descent_then_persistent_slumped_low_posture',
                'reference_t':round(ref['t'],6),'preceding_phase':ref['base'],'descent_seconds':round(elapsed,6),
                'low_posture_seconds':round(t-tail[0]['t'],6),'observed_low_frames':len(tail),
                'hip_drop_normalized':round(float((o['hip'][1]-ref['hip'][1])/ref['height']),6),
                'shoulder_drop_normalized':round(float((o['shoulder'][1]-ref['shoulder'][1])/ref['height']),6),
                'peak_hip_drop_speed_normalized':round(peak_hip,6),
                'fall_transition_observed':False,'descent_observed':True,'floor_contact_observed':False,'cause_known':False,
                'confidence_kind':'minimum_observed_pose_and_required_joint_scores_not_event_probability',
                'limitation':'Slow observed descent and persistent slumped low posture only. Does not establish floor contact, dizziness or accidental fall; visual review required.'})
        return None
