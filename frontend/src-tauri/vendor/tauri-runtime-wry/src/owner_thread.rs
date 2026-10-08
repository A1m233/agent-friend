// Copyright 2026 agent-friend contributors
// SPDX-License-Identifier: Apache-2.0 OR MIT

//! Windows runtime state has one owner thread. Tokens never own its payload.
//!
//! The registry contains only thread-local weak references. Upgrading one yields
//! an Rc-backed borrow which cannot leave the owner thread. Removing an owner
//! revokes future borrows; an active/reentrant borrow keeps the value alive until
//! that callback completes, and its final destructor still runs on this thread.

use std::{
  any::Any,
  cell::RefCell,
  collections::HashMap,
  fmt,
  marker::PhantomData,
  ops::Deref,
  rc::{Rc, Weak},
  sync::{
    atomic::{AtomicBool, AtomicU64, Ordering},
    Arc,
  },
  thread::{self, ThreadId},
};

thread_local! {
  static REGISTRY: RefCell<HashMap<u64, Weak<dyn Any>>> = RefCell::new(HashMap::new());
}
static NEXT_KEY: AtomicU64 = AtomicU64::new(1);

struct Identity {
  thread: ThreadId,
  key: u64,
  alive: AtomicBool,
}

pub(crate) struct Owner<T: 'static> {
  // Rc makes the owner and all payload borrows !Send and !Sync.
  value: Rc<T>,
  token: Token<T>,
}

pub(crate) struct Token<T: 'static> {
  identity: Arc<Identity>,
  // The token identifies T without owning T or inheriting its auto traits.
  marker: PhantomData<fn() -> T>,
}

pub(crate) struct OwnerBorrow<T: 'static>(Rc<T>);

impl<T> Deref for OwnerBorrow<T> {
  type Target = T;

  fn deref(&self) -> &T {
    &self.0
  }
}

impl<T> Owner<T> {
  pub(crate) fn new(value: T) -> Self {
    let key = NEXT_KEY
      .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |key| {
        key.checked_add(1)
      })
      .expect("runtime owner identity exhausted");
    let value = Rc::new(value);
    let erased: Rc<dyn Any> = value.clone();
    REGISTRY.with(|registry| {
      registry.borrow_mut().insert(key, Rc::downgrade(&erased));
    });
    Self {
      value,
      token: Token {
        identity: Arc::new(Identity {
          thread: thread::current().id(),
          key,
          alive: AtomicBool::new(true),
        }),
        marker: PhantomData,
      },
    }
  }

  pub(crate) fn token(&self) -> Token<T> {
    self.token.clone()
  }
}

impl<T> Drop for Owner<T> {
  fn drop(&mut self) {
    self.token.identity.alive.store(false, Ordering::Release);
    // TLS may already be in teardown. Revocation still happens before T drops.
    let _ = REGISTRY.try_with(|registry| {
      registry.borrow_mut().remove(&self.token.identity.key);
    });
    // Keep this field visibly owned until after revocation. Its automatic drop,
    // like every OwnerBorrow drop, occurs only on the original thread.
    let _ = &self.value;
  }
}

impl<T> Clone for Token<T> {
  fn clone(&self) -> Self {
    Self {
      identity: self.identity.clone(),
      marker: PhantomData,
    }
  }
}

impl<T> fmt::Debug for Token<T> {
  fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
    f.debug_struct("OwnerThreadToken")
      .field("thread", &self.identity.thread)
      .field("key", &self.identity.key)
      .field("alive", &self.is_alive())
      .finish()
  }
}

impl<T> Token<T> {
  pub(crate) fn is_alive(&self) -> bool {
    self.identity.alive.load(Ordering::Acquire)
  }

  pub(crate) fn borrow(&self) -> Option<OwnerBorrow<T>> {
    if thread::current().id() != self.identity.thread || !self.is_alive() {
      return None;
    }
    // Release the registry borrow before invoking any application/native code.
    let value = REGISTRY
      .try_with(|registry| {
        registry
          .borrow()
          .get(&self.identity.key)
          .and_then(Weak::upgrade)
      })
      .ok()??;
    value.downcast::<T>().ok().map(OwnerBorrow)
  }

  #[cfg(test)]
  fn with<R>(&self, operation: impl FnOnce(&T) -> R) -> Option<R> {
    self.borrow().map(|value| operation(&value))
  }
}

#[cfg(test)]
mod tests {
  use super::*;
  use std::sync::{mpsc, Mutex};

  struct ThreadBoundValue {
    creator: ThreadId,
    drops: Arc<Mutex<Vec<ThreadId>>>,
    _not_send: Rc<()>,
  }

  impl Drop for ThreadBoundValue {
    fn drop(&mut self) {
      assert_eq!(thread::current().id(), self.creator);
      self.drops.lock().unwrap().push(thread::current().id());
    }
  }

  fn fixture() -> (Owner<ThreadBoundValue>, Arc<Mutex<Vec<ThreadId>>>) {
    let drops = Arc::new(Mutex::new(Vec::new()));
    let owner = Owner::new(ThreadBoundValue {
      creator: thread::current().id(),
      drops: drops.clone(),
      _not_send: Rc::new(()),
    });
    (owner, drops)
  }

  #[test]
  fn background_tokens_never_access_clone_or_drop_the_payload() {
    fn send_sync<T: Send + Sync>() {}
    send_sync::<Token<ThreadBoundValue>>();
    let (owner, drops) = fixture();
    let token = owner.token();
    thread::spawn(move || {
      for _ in 0..10_000 {
        let copy = token.clone();
        assert!(copy.borrow().is_none());
        assert!(format!("{copy:?}").contains("OwnerThreadToken"));
      }
      assert!(token.with(|_| panic!("off-thread access")).is_none());
    })
    .join()
    .unwrap();
    assert!(drops.lock().unwrap().is_empty());
    assert_eq!(Rc::strong_count(&owner.value), 1);
    drop(owner);
    assert_eq!(*drops.lock().unwrap(), [thread::current().id()]);
  }

  #[test]
  fn owner_drops_payload_even_when_last_token_drops_on_worker() {
    let (owner, drops) = fixture();
    let token = owner.token();
    let (tx, rx) = mpsc::channel();
    let worker = thread::spawn(move || {
      rx.recv().unwrap();
      assert!(!token.is_alive());
      assert!(token.borrow().is_none());
      drop(token);
    });
    drop(owner);
    assert_eq!(*drops.lock().unwrap(), [thread::current().id()]);
    tx.send(()).unwrap();
    worker.join().unwrap();
    assert_eq!(drops.lock().unwrap().len(), 1);
  }

  #[test]
  fn reentrant_owner_release_preserves_active_borrow_and_revokes_new_borrows() {
    let (owner, drops) = fixture();
    let token = owner.token();
    let owner = RefCell::new(Some(owner));
    token
      .with(|value| {
        token
          .with(|nested| assert!(std::ptr::eq(value, nested)))
          .unwrap();
        drop(owner.borrow_mut().take());
        assert!(!token.is_alive());
        assert!(token.borrow().is_none());
        assert!(drops.lock().unwrap().is_empty());
        assert_eq!(value.creator, thread::current().id());
      })
      .unwrap();
    assert_eq!(*drops.lock().unwrap(), [thread::current().id()]);
  }

  #[test]
  fn token_survives_creator_thread_exit_without_owning_its_value() {
    let drops = Arc::new(Mutex::new(Vec::new()));
    let worker_drops = drops.clone();
    let (token, creator) = thread::spawn(move || {
      let creator = thread::current().id();
      let owner = Owner::new(ThreadBoundValue {
        creator,
        drops: worker_drops,
        _not_send: Rc::new(()),
      });
      (owner.token(), creator)
    })
    .join()
    .unwrap();
    assert_eq!(*drops.lock().unwrap(), [creator]);
    assert!(!token.is_alive());
    assert!(token.borrow().is_none());
    drop(token);
    assert_eq!(drops.lock().unwrap().len(), 1);
  }

  #[test]
  fn creator_tls_teardown_revokes_tokens_and_drops_payload() {
    thread_local! {
      static TLS_OWNER: RefCell<Option<Owner<ThreadBoundValue>>> = const { RefCell::new(None) };
    }
    let drops = Arc::new(Mutex::new(Vec::new()));
    let worker_drops = drops.clone();
    let (token, creator) = thread::spawn(move || {
      let creator = thread::current().id();
      let owner = Owner::new(ThreadBoundValue {
        creator,
        drops: worker_drops,
        _not_send: Rc::new(()),
      });
      let token = owner.token();
      TLS_OWNER.with(|slot| *slot.borrow_mut() = Some(owner));
      (token, creator)
    })
    .join()
    .unwrap();
    assert_eq!(*drops.lock().unwrap(), [creator]);
    assert!(!token.is_alive());
    assert!(token.borrow().is_none());
  }

  #[test]
  fn independent_owners_do_not_alias_or_revalidate_revoked_tokens() {
    let first = Owner::new(41u32);
    let revoked = first.token();
    drop(first);
    let second = Owner::new(42u32);
    assert!(revoked.borrow().is_none());
    assert_eq!(second.token().with(|value| *value), Some(42));
  }
}
